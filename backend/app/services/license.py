"""Device fingerprint + license token checks (no user accounts).

Poll quota is stored in ``device_poll_events`` so multi-worker deployments share
the same hourly counters. A short in-process cache reduces DB load on snapshots.

Cloud account slots use ``device_quota_state`` reconciled against live ``accounts``
rows so upgrades / failed creates cannot permanently drift past the cap.

SQLite multi-worker note: ``with_for_update`` is limited on SQLite; poll and cloud
quota use a per-device state row as a serialization point. Prefer Postgres (or a
single writer) for multi-worker deployments that need hard caps under load.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Account, DevicePollEvent, DevicePollQuotaState, DeviceQuotaState


def _session_factory():
    """Resolve SessionLocal at call time so tests can rebind app.db.SessionLocal."""
    from app.db import SessionLocal

    return SessionLocal


logger = logging.getLogger("openmail.license")

# Snapshot cache only (not authority): device_id -> (expires_at_unix, used_count)
_snap_cache: dict[str, tuple[float, int]] = {}
_snap_lock = Lock()
_SNAP_TTL_SEC = 2.0


def _as_str(value: object | None) -> str:
    """Coerce header/query values; ignore FastAPI Header defaults if mis-passed."""
    if value is None:
        return ""
    if not isinstance(value, str):
        # e.g. starlette Header() sentinel mistakenly forwarded as token
        return ""
    return value.strip()


def is_licensed(
    *,
    device_id: str | None,
    license_token: str | None,
    settings: Settings | None = None,
) -> bool:
    """Return True if request presents a valid unlimited license."""
    s = settings or get_settings()
    token = _as_str(license_token)
    if not token:
        return False
    if token in s.license_token_set:
        return True
    secret = (s.license_hmac_secret or "").strip()
    fp = _as_str(device_id)
    if secret and fp:
        expected = hmac.new(
            secret.encode("utf-8"),
            fp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, token.lower()) or hmac.compare_digest(
            expected, token
        ):
            return True
    return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _device_key(device_id: str | None) -> str:
    return (device_id or "anonymous").strip() or "anonymous"


def _count_polls(db: Session, device_id: str, *, since: datetime) -> int:
    n = db.scalar(
        select(func.count())
        .select_from(DevicePollEvent)
        .where(DevicePollEvent.device_id == device_id, DevicePollEvent.ts >= since)
    )
    return int(n or 0)


def _live_cloud_count(db: Session, device_id: str) -> int:
    n = db.scalar(
        select(func.count()).select_from(Account).where(Account.owner_user_id == device_id)
    )
    return int(n or 0)


def _is_sqlite_bind(db: Session) -> bool:
    try:
        bind = db.get_bind()
        return bool(bind is not None and bind.dialect.name == "sqlite")
    except Exception:
        return False


def _begin_immediate_if_sqlite(db: Session) -> None:
    """Take a SQLite write lock so concurrent workers serialize quota checks."""
    if not _is_sqlite_bind(db):
        return
    if db.in_transaction():
        # Nested callers already hold a transaction; cannot upgrade mid-flight.
        return
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except Exception:
        # Tests / drivers that auto-begin — ignore and rely on row lock best-effort.
        logger.debug("BEGIN IMMEDIATE unavailable", exc_info=True)


def _ensure_poll_state_locked(db: Session, device_id: str) -> DevicePollQuotaState:
    """Load or create DevicePollQuotaState, taking a row lock when supported."""
    row = (
        db.query(DevicePollQuotaState)
        .filter(DevicePollQuotaState.device_id == device_id)
        .with_for_update()
        .one_or_none()
    )
    if row is not None:
        return row
    row = DevicePollQuotaState(device_id=device_id)
    db.add(row)
    try:
        db.flush()
    except Exception:
        db.rollback()
        row = (
            db.query(DevicePollQuotaState)
            .filter(DevicePollQuotaState.device_id == device_id)
            .with_for_update()
            .one()
        )
    return row


def _ensure_quota_state_locked(db: Session, device_id: str) -> DeviceQuotaState:
    """Load or create DeviceQuotaState, taking a row lock when supported."""
    row = (
        db.query(DeviceQuotaState)
        .filter(DeviceQuotaState.device_id == device_id)
        .with_for_update()
        .one_or_none()
    )
    if row is not None:
        return row
    row = DeviceQuotaState(device_id=device_id, cloud_accounts_used=0)
    db.add(row)
    try:
        db.flush()
    except Exception:
        db.rollback()
        row = (
            db.query(DeviceQuotaState)
            .filter(DeviceQuotaState.device_id == device_id)
            .with_for_update()
            .one()
        )
    return row


def reconcile_cloud_account_used(db: Session, device_id: str) -> int:
    """Set cloud_accounts_used to the live account count; return the reconciled value."""
    live = _live_cloud_count(db, device_id)
    row = (
        db.query(DeviceQuotaState)
        .filter(DeviceQuotaState.device_id == device_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        if live == 0:
            return 0
        row = DeviceQuotaState(device_id=device_id, cloud_accounts_used=live)
        db.add(row)
    else:
        row.cloud_accounts_used = live
    db.flush()
    return live


def _prune_old(db: Session, *, older_than: datetime) -> None:
    """Best-effort delete of events outside the rolling window (+ slack)."""
    try:
        db.execute(delete(DevicePollEvent).where(DevicePollEvent.ts < older_than))
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("poll event prune failed", exc_info=True)


def check_poll_quota(
    device_id: str | None,
    *,
    license_token: str | None = None,
    settings: Settings | None = None,
    db: Session | None = None,
) -> tuple[bool, str | None]:
    """Return (allowed, error_message). Licensed devices skip limits.

    On success, records one poll event for this device (hourly window).

    Serialization: lock ``device_poll_quota_state`` for the device (and BEGIN IMMEDIATE
    on SQLite when we own the session) so concurrent workers cannot all observe
    ``used < limit`` then all insert.
    """
    s = settings or get_settings()
    if is_licensed(device_id=device_id, license_token=_as_str(license_token) or None, settings=s):
        return True, None
    did = _device_key(device_id)
    # Tolerate incomplete settings mocks in unit tests (SimpleNamespace())
    limit = max(1, int(getattr(s, "quota_max_poll_per_hour", None) or 120))
    now = _utcnow()
    since = now - timedelta(hours=1)
    prune_before = now - timedelta(hours=2)

    own_session = db is None
    session = db or _session_factory()()
    try:
        if own_session:
            _begin_immediate_if_sqlite(session)
        # Per-device row acts as a mutex for the count+insert critical section.
        _ensure_poll_state_locked(session, did)
        used = _count_polls(session, did, since=since)
        if used >= limit:
            if own_session:
                session.rollback()
            return False, f"poll quota exceeded ({limit}/hour); use a license token"
        session.add(DevicePollEvent(device_id=did, ts=now))
        session.commit()
        # Occasional prune (1/32 of requests) to keep table small
        if (hash(did) ^ int(now.timestamp())) % 32 == 0:
            _prune_old(session, older_than=prune_before)
        with _snap_lock:
            _snap_cache[did] = (time.time() + _SNAP_TTL_SEC, used + 1)
        return True, None
    except Exception:
        session.rollback()
        logger.exception("poll quota check failed; denying request")
        return False, "poll quota unavailable"
    finally:
        if own_session:
            session.close()


def poll_used_in_hour(
    device_id: str | None,
    *,
    settings: Settings | None = None,
    db: Session | None = None,
) -> int:
    """How many polls this device used in the last hour (for quota snapshot)."""
    did = _device_key(device_id)
    now_mono = time.time()
    with _snap_lock:
        hit = _snap_cache.get(did)
        if hit and hit[0] > now_mono:
            return hit[1]

    own_session = db is None
    session = db or _session_factory()()
    try:
        since = _utcnow() - timedelta(hours=1)
        used = _count_polls(session, did, since=since)
        with _snap_lock:
            _snap_cache[did] = (now_mono + _SNAP_TTL_SEC, used)
        return used
    except Exception:
        logger.debug("poll_used_in_hour failed", exc_info=True)
        return 0
    finally:
        if own_session:
            session.close()


def quota_snapshot(
    *,
    device_id: str | None = None,
    license_token: str | None = None,
    settings: Settings | None = None,
    cloud_used: int | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    licensed = is_licensed(device_id=device_id, license_token=license_token, settings=s)
    used = 0 if licensed else poll_used_in_hour(device_id, settings=s, db=db)
    out: dict[str, Any] = {
        "licensed": licensed,
        "max_local_accounts": None if licensed else s.quota_max_local_accounts,
        "max_cloud_accounts": None if licensed else s.quota_max_cloud_accounts,
        "max_poll_per_hour": None if licensed else s.quota_max_poll_per_hour,
        "poll_used_hour": used,
        "fetch_lookback_days": s.fetch_default_lookback_days,
        "mail_retention_days": s.mail_retention_days,
        "auth_ui_enabled": s.auth_ui_enabled,
    }
    if cloud_used is not None:
        out["cloud_used"] = cloud_used
    return out


def reserve_cloud_account_slot(
    db: Session,
    device_id: str,
    *,
    settings: Settings | None = None,
    license_token: str | None = None,
) -> None:
    """Reserve one cloud account slot, enforcing against live account inventory.

    Counter is floored by ``COUNT(accounts)`` so upgrades without a backfill and
    prior slot leaks cannot exceed the cap by 2×. The reservation and the
    subsequent account INSERT should share the same transaction so rollback
    undoes the counter bump.

    Serialization:
    - Prefer SQLite ``BEGIN IMMEDIATE`` when the session is not already open
      (strong write lock for multi-worker SQLite).
    - Always finish with a conditional ``UPDATE … WHERE used < cap`` so two
      concurrent transactions cannot both bump past the cap even when
      ``with_for_update`` is a no-op (SQLite deferred) or weak.
    """
    s = settings or get_settings()
    if is_licensed(device_id=device_id, license_token=license_token, settings=s):
        return
    cap = max(0, int(getattr(s, "quota_max_cloud_accounts", None) or 0))
    # Stronger SQLite multi-worker path when we can still take the write lock.
    _begin_immediate_if_sqlite(db)
    live = _live_cloud_count(db, device_id)
    row = _ensure_quota_state_locked(db, device_id)
    # Floor counter to live inventory first (upgrade / drift repair).
    used = int(row.cloud_accounts_used or 0)
    if live > used:
        row.cloud_accounts_used = live
        db.flush()
        used = live
    if used >= cap:
        raise ValueError(
            f"cloud account quota exceeded ({used}/{cap}); use a license token"
        )
    # Atomic conditional increment — rowcount 0 means lost the race or at cap.
    result = db.execute(
        update(DeviceQuotaState)
        .where(
            DeviceQuotaState.device_id == device_id,
            DeviceQuotaState.cloud_accounts_used < cap,
        )
        .values(cloud_accounts_used=DeviceQuotaState.cloud_accounts_used + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.expire(row)
        current = (
            db.query(DeviceQuotaState.cloud_accounts_used)
            .filter(DeviceQuotaState.device_id == device_id)
            .scalar()
        )
        shown = int(current if current is not None else max(used, live))
        raise ValueError(
            f"cloud account quota exceeded ({shown}/{cap}); use a license token"
        )
    db.expire(row)


def release_cloud_account_slot(db: Session, device_id: str) -> None:
    """Decrement one reserved slot after a flushed account delete.

    Uses a row lock (and conditional UPDATE) so concurrent create reservations
    serialize with the decrement. Does not rewrite the counter from
    ``COUNT(accounts)`` — that would clobber an in-flight reserve.
    """
    _begin_immediate_if_sqlite(db)
    row = (
        db.query(DeviceQuotaState)
        .filter(DeviceQuotaState.device_id == device_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        live = _live_cloud_count(db, device_id)
        if live > 0:
            db.add(DeviceQuotaState(device_id=device_id, cloud_accounts_used=live))
            db.flush()
        return
    result = db.execute(
        update(DeviceQuotaState)
        .where(
            DeviceQuotaState.device_id == device_id,
            DeviceQuotaState.cloud_accounts_used > 0,
        )
        .values(cloud_accounts_used=DeviceQuotaState.cloud_accounts_used - 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        # Already at 0 — keep floor.
        row.cloud_accounts_used = 0
        db.flush()
        return
    db.expire(row)
