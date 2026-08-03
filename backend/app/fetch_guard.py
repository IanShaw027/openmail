"""Engineering floor for fetch: min-interval and serial-per-account hooks.

Product rate-limit / quotas are intentionally not implemented.
These helpers prevent concurrent cookie storms and thrashing upstream.

In-flight state uses a time-bounded, token-owned database lease so process
crashes recover and multiple API workers acquire the account lock atomically.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Generator
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import FetchLockState

# In-process mutexes reduce contention inside one worker. Cross-worker safety
# relies on DB lease_token + conditional UPDATE (see _acquire_lease).
_locks: dict[str, Lock] = {}
_locks_guard = Lock()

# Default lease: if a worker dies mid-fetch, others may proceed after this.
_DEFAULT_LEASE_SECONDS = 180.0


def _get_lock(account_id: str) -> Lock:
    with _locks_guard:
        if account_id not in _locks:
            _locks[account_id] = Lock()
        return _locks[account_id]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class FetchTooSoonError(Exception):
    """Raised when real upstream fetch is blocked by min-interval."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"fetch too soon; retry after {retry_after:.1f}s")


class FetchInFlightError(Exception):
    """Raised when another fetch for the same account is already running."""

    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__("fetch already in flight for account")


def seconds_until_allowed(
    db: Session,
    account_id: str,
    *,
    settings: Settings | None = None,
) -> float:
    s = settings or get_settings()
    state = db.get(FetchLockState, account_id)
    if state is None or state.last_real_fetch_at is None:
        return 0.0
    last = _as_utc(state.last_real_fetch_at)
    if last is None:
        return 0.0
    elapsed = (_utcnow() - last).total_seconds()
    remaining = s.fetch_min_interval_seconds - elapsed
    return max(0.0, remaining)


def _lease_seconds(settings: Settings) -> float:
    raw = getattr(settings, "fetch_lock_lease_seconds", None)
    try:
        n = float(raw) if raw is not None else _DEFAULT_LEASE_SECONDS
    except (TypeError, ValueError):
        n = _DEFAULT_LEASE_SECONDS
    return max(30.0, min(n, 3600.0))


def _lease_active(state: FetchLockState, *, lease_seconds: float) -> bool:
    """True if in_flight is set and the lease has not expired."""
    if not state.in_flight:
        return False
    updated = _as_utc(state.updated_at)
    if updated is None:
        # Unknown age — treat as expired so we never stick forever
        return False
    age = (_utcnow() - updated).total_seconds()
    return age < lease_seconds


def _ensure_lock_row(db: Session, account_id: str) -> None:
    if db.get(FetchLockState, account_id) is not None:
        return
    db.add(FetchLockState(account_id=account_id, in_flight=False))
    try:
        db.commit()
    except IntegrityError:
        # Another process inserted the same primary key first.
        db.rollback()


def _acquire_lease(
    db: Session,
    account_id: str,
    *,
    lease_seconds: float,
) -> tuple[str, datetime]:
    """Atomically acquire an expired/free DB lease and return its ownership token."""
    _ensure_lock_row(db, account_id)
    now = _utcnow()
    cutoff = now - timedelta(seconds=lease_seconds)
    token = str(uuid4())
    result = db.execute(
        update(FetchLockState)
        .where(
            FetchLockState.account_id == account_id,
            or_(
                FetchLockState.in_flight.is_(False),
                FetchLockState.updated_at <= cutoff,
            ),
        )
        .values(in_flight=True, lease_token=token, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        state = db.get(FetchLockState, account_id)
        retry_after: float | None = None
        if state is not None and _lease_active(state, lease_seconds=lease_seconds):
            updated = _as_utc(state.updated_at) or now
            retry_after = max(0.0, lease_seconds - (now - updated).total_seconds())
        raise FetchInFlightError(retry_after=retry_after)
    db.commit()
    return token, now


def lease_is_current(
    db: Session,
    account_id: str,
    lease_token: str,
    *,
    settings: Settings | None = None,
) -> bool:
    """True when the caller still owns an active lease for this account."""
    s = settings or get_settings()
    lease_s = _lease_seconds(s)
    state = db.get(FetchLockState, account_id)
    if state is None:
        return False
    if state.lease_token != lease_token:
        return False
    return _lease_active(state, lease_seconds=lease_s)


@contextmanager
def account_fetch_slot(
    db: Session,
    account_id: str,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> Generator[str, None, None]:
    """Acquire serial lock + enforce min-interval for a real upstream fetch.

    Usage:
        with account_fetch_slot(db, account.id):
            result = provider.fetch(...)
    """
    s = settings or get_settings()
    lease_s = _lease_seconds(s)
    lock = _get_lock(account_id)
    if not lock.acquire(blocking=False):
        # Align with DB-lease path so clients get a consistent backoff hint.
        wait = seconds_until_allowed(db, account_id, settings=s)
        raise FetchInFlightError(retry_after=wait if wait > 0 else min(5.0, lease_s))
    try:
        if not force:
            wait = seconds_until_allowed(db, account_id, settings=s)
            if wait > 0:
                raise FetchTooSoonError(wait)

        lease_token, _ = _acquire_lease(db, account_id, lease_seconds=lease_s)
        # Re-check min-interval after winning the lease so two workers that both
        # passed the pre-lease check cannot both complete real fetches too close.
        if not force:
            wait = seconds_until_allowed(db, account_id, settings=s)
            if wait > 0:
                try:
                    db.execute(
                        update(FetchLockState)
                        .where(
                            FetchLockState.account_id == account_id,
                            FetchLockState.lease_token == lease_token,
                        )
                        .values(
                            in_flight=False,
                            lease_token=None,
                            updated_at=_utcnow(),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                raise FetchTooSoonError(wait)

        completed = False
        try:
            yield lease_token
            completed = True
        finally:
            # Ownership predicate prevents an expired worker from clearing a
            # newer lease that another process has already taken over.
            try:
                now = _utcnow()
                values: dict[str, object] = {
                    "in_flight": False,
                    "lease_token": None,
                    "updated_at": now,
                }
                if completed:
                    values["last_real_fetch_at"] = now
                db.execute(
                    update(FetchLockState)
                    .where(
                        FetchLockState.account_id == account_id,
                        FetchLockState.lease_token == lease_token,
                    )
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        lock.release()
