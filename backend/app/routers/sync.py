"""Device-scoped cloud sync HTTP API (status + incremental delta pull).

POST /api/me/sync remains 410 for compatibility (user-scoped trigger removed).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.crypto import decrypt_str_or_plain
from app.deps import DbDep
from app.deps_device import device_id_strict
from app.models import Account, MailItem
from app.services.credentials import is_client_sealed_blob, load_credentials
from app.services.mail_store import list_delta
from app.services.settings_service import get_effective_settings
from app.services.sync_worker import get_sync_worker

router = APIRouter(tags=["sync"])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _accounts_for_status(db: Session, device_id: str) -> list[dict]:
    """Accounts owned by device that are sync-enabled or already have mail rows."""
    mail_counts = (
        db.query(MailItem.account_id, func.count(MailItem.id).label("cnt"))
        .join(Account, Account.id == MailItem.account_id)
        .filter(Account.owner_user_id == device_id)
        .group_by(MailItem.account_id)
        .all()
    )
    count_by_id = {aid: int(cnt) for aid, cnt in mail_counts}
    account_ids_with_mail = set(count_by_id.keys())

    q = db.query(Account).filter(Account.owner_user_id == device_id)
    if account_ids_with_mail:
        q = q.filter(
            or_(
                Account.sync_enabled.is_(True),
                Account.id.in_(account_ids_with_mail),
            )
        )
    else:
        q = q.filter(Account.sync_enabled.is_(True))
    rows = q.order_by(Account.email.asc()).all()

    out: list[dict] = []
    for acc in rows:
        sealed = False
        try:
            sealed = is_client_sealed_blob(load_credentials(acc))
        except Exception:
            sealed = False
        out.append(
            {
                "id": acc.id,
                "email": acc.email,
                "sync_enabled": bool(acc.sync_enabled),
                "last_sync_at": _iso(acc.last_sync_at),
                "last_sync_error": acc.last_sync_error,
                "mail_count": count_by_id.get(acc.id, 0),
                "client_sealed": sealed,
            }
        )
    return out


def _accounts_meta_for_delta(
    db: Session,
    device_id: str,
    *,
    referenced_account_ids: set[str],
) -> list[dict]:
    """Account meta: all sync_enabled + any referenced in this delta page."""
    q = db.query(Account).filter(Account.owner_user_id == device_id)
    if referenced_account_ids:
        q = q.filter(
            or_(
                Account.sync_enabled.is_(True),
                Account.id.in_(referenced_account_ids),
            )
        )
    else:
        q = q.filter(Account.sync_enabled.is_(True))

    rows = q.order_by(Account.email.asc()).all()
    return [
        {
            "id": acc.id,
            "email": acc.email,
            "latest_verification_code": decrypt_str_or_plain(acc.latest_verification_code),
            "latest_code_at": _iso(acc.latest_code_at),
            "last_sync_at": _iso(acc.last_sync_at),
            "last_sync_error": acc.last_sync_error,
        }
        for acc in rows
    ]


@router.get("/api/sync/status")
def sync_status(
    db: DbDep,
    device_id: str = Depends(device_id_strict),
) -> dict:
    """Worker + global flag + per-device account sync health."""
    worker = get_sync_worker()
    eff = get_effective_settings(db)
    return {
        "worker_alive": bool(worker.is_alive),
        "sync_enabled_global": bool(eff.sync_enabled_global),
        "accounts": _accounts_for_status(db, device_id),
    }


@router.get("/api/sync/delta")
def sync_delta(
    db: DbDep,
    device_id: str = Depends(device_id_strict),
    since: str | None = Query(default=None, description="ISO datetime keyset lower bound"),
    since_id: str | None = Query(default=None, description="Mail id tie-breaker for keyset"),
    limit: int = Query(default=200, ge=1, le=500),
    include_body: bool = Query(default=True),
) -> dict:
    """Incremental mail_items pull for this vault device (HMAC required)."""
    delta = list_delta(
        db,
        device_id,
        since=since,
        since_id=since_id,
        limit=limit,
        include_body=include_body,
    )
    referenced = {m["account_id"] for m in delta.get("mails") or [] if m.get("account_id")}
    accounts = _accounts_meta_for_delta(db, device_id, referenced_account_ids=referenced)
    return {
        "server_time": delta.get("server_time"),
        "server_seq": None,
        "has_more": bool(delta.get("has_more")),
        "mails": delta.get("mails") or [],
        "accounts": accounts,
    }


@router.post("/api/me/sync")
def trigger_my_sync() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="user sync removed — fetch from console; local cache powers search",
    )
