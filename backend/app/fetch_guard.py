"""Engineering floor for fetch: min-interval and serial-per-account hooks.

Product rate-limit / quotas are intentionally not implemented.
These helpers prevent concurrent cookie storms and thrashing upstream.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Generator

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import FetchLockState

# In-process mutexes (single-instance deployment). Extend with Redis later if needed.
_locks: dict[str, Lock] = {}
_locks_guard = Lock()


def _get_lock(account_id: str) -> Lock:
    with _locks_guard:
        if account_id not in _locks:
            _locks[account_id] = Lock()
        return _locks[account_id]


class FetchTooSoonError(Exception):
    """Raised when real upstream fetch is blocked by min-interval."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"fetch too soon; retry after {retry_after:.1f}s")


class FetchInFlightError(Exception):
    """Raised when another fetch for the same account is already running."""

    def __init__(self) -> None:
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
    last = state.last_real_fetch_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    remaining = s.fetch_min_interval_seconds - elapsed
    return max(0.0, remaining)


@contextmanager
def account_fetch_slot(
    db: Session,
    account_id: str,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> Generator[None, None, None]:
    """Acquire serial lock + enforce min-interval for a real upstream fetch.

    Usage:
        with account_fetch_slot(db, account.id):
            result = provider.fetch(...)
    """
    s = settings or get_settings()
    lock = _get_lock(account_id)
    if not lock.acquire(blocking=False):
        raise FetchInFlightError()
    try:
        state = db.get(FetchLockState, account_id)
        if state is None:
            state = FetchLockState(account_id=account_id, in_flight=False)
            db.add(state)
            db.flush()

        if not force:
            wait = seconds_until_allowed(db, account_id, settings=s)
            if wait > 0:
                raise FetchTooSoonError(wait)

        if state.in_flight:
            raise FetchInFlightError()

        state.in_flight = True
        state.updated_at = datetime.now(timezone.utc)
        db.commit()
        try:
            yield
            state.last_real_fetch_at = datetime.now(timezone.utc)
        finally:
            state.in_flight = False
            state.updated_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        lock.release()
