"""SyncWorker path A: polled mail persists to mail_items + sync_cursors."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models import Account, AccountPool, AccountStatus, MailItem, ProviderType, SyncCursor
from app.providers.base import Message
from app.services.fetch_service import FetchServiceResult
from app.services.sync_worker import get_sync_worker


def _make_account(db: Session, *, email: str = "sync-store@example.com") -> Account:
    acc = Account(
        email=email,
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
        owner_user_id="vk_device_sync_mail_store",
        status=AccountStatus.ok,
        sync_enabled=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def test_sync_account_upserts_mail_items_and_cursor(db_session: Session) -> None:
    """Mocked fetch returns 2 messages → MailItem rows + SyncCursor high water."""
    acc = _make_account(db_session)
    account_id = acc.id
    t1 = "2026-06-01T10:00:00+00:00"
    t2 = "2026-06-01T12:00:00+00:00"

    def _fake_fetch(db, account, **kwargs):  # type: ignore[no-untyped-def]
        folder = kwargs.get("folder", "inbox")
        # Only return messages for first folder to keep assertion simple
        if folder not in ("inbox", "INBOX"):
            return FetchServiceResult(
                ok=True,
                messages=[],
                message_count=0,
                folder=folder,
                account_id=account.id,
                email=account.email,
            )
        msgs = [
            Message(
                id="msg-a",
                subject="First",
                from_="a@b.com",
                from_address="a@b.com",
                date=t1,
                body_text="hello a",
                body_preview="hello a",
                folder=folder,
            ),
            Message(
                id="msg-b",
                subject="Second code 654321",
                from_="b@c.com",
                from_address="b@c.com",
                date=t2,
                body_text="code 654321",
                body_preview="code 654321",
                verification_code="654321",
                folder=folder,
            ),
        ]
        return FetchServiceResult(
            ok=True,
            messages=msgs,
            message_count=2,
            folder=folder,
            account_id=account.id,
            email=account.email,
            code="654321",
        )

    with patch("app.services.sync_worker.fetch_account", side_effect=_fake_fetch):
        worker = get_sync_worker()
        detail = worker.sync_one_account(account_id, force=True)

    assert detail["ok"] is True
    assert detail["account_id"] == account_id

    folders = {f["folder"]: f for f in detail.get("folders") or []}
    assert "inbox" in folders
    inbox = folders["inbox"]
    assert inbox["ok"] is True
    assert inbox["message_count"] == 2
    assert inbox.get("inserted") == 2
    assert inbox.get("updated", 0) == 0
    assert inbox.get("high_water_time") is not None

    db_session.expire_all()
    items = (
        db_session.query(MailItem)
        .filter(MailItem.account_id == account_id, MailItem.folder == "inbox")
        .all()
    )
    assert len(items) == 2
    sids = {i.stable_id for i in items}
    assert "p:msg-a" in sids
    assert "p:msg-b" in sids

    cursors = (
        db_session.query(SyncCursor)
        .filter(SyncCursor.account_id == account_id, SyncCursor.folder == "inbox")
        .all()
    )
    assert len(cursors) == 1
    cur = cursors[0]
    assert cur.mode == "time"
    data = json.loads(cur.cursor_json or "{}")
    assert "high_water_time" in data
    # High water should be the later message time
    hw = data["high_water_time"]
    assert "2026-06-01T12:00:00" in hw
    assert "p:msg-b" in (data.get("high_water_ids") or [])

    refreshed = db_session.get(Account, account_id)
    assert refreshed is not None
    assert refreshed.last_sync_at is not None
    assert refreshed.last_sync_error is None


def test_sync_account_incremental_uses_since_overlap(db_session: Session) -> None:
    """When a cursor exists, second sync passes since = high_water - OVERLAP."""
    from app.services.mail_store import save_cursor_time_high_water
    from app.services.sync_worker import OVERLAP_SECONDS

    acc = _make_account(db_session, email="incr@example.com")
    account_id = acc.id
    hw = datetime(2026, 7, 1, 15, 0, 0, tzinfo=timezone.utc)
    save_cursor_time_high_water(
        db_session, account_id, "inbox", hw, high_water_ids=["p:old"]
    )
    db_session.commit()

    seen_kwargs: list[dict] = []

    def _fake_fetch(db, account, **kwargs):  # type: ignore[no-untyped-def]
        seen_kwargs.append(dict(kwargs))
        folder = kwargs.get("folder", "inbox")
        if folder not in ("inbox", "INBOX"):
            return FetchServiceResult(
                ok=True, messages=[], message_count=0, folder=folder
            )
        msgs = [
            Message(
                id="msg-new",
                subject="New",
                from_="n@e.com",
                date="2026-07-01T16:00:00+00:00",
                body_text="new mail",
            )
        ]
        return FetchServiceResult(
            ok=True,
            messages=msgs,
            message_count=1,
            folder=folder,
            account_id=account.id,
            email=account.email,
        )

    with patch("app.services.sync_worker.fetch_account", side_effect=_fake_fetch):
        worker = get_sync_worker()
        detail = worker.sync_one_account(account_id, force=True)

    assert detail["ok"] is True
    inbox_calls = [k for k in seen_kwargs if k.get("folder") in ("inbox", "INBOX", None)]
    # folder is always set by _sync_folder
    inbox_calls = [k for k in seen_kwargs if str(k.get("folder", "")).lower() in ("inbox",)]
    assert inbox_calls, f"expected inbox fetch calls, got {seen_kwargs}"
    call = inbox_calls[0]
    assert call.get("since") is not None
    assert call.get("max_messages") == 50
    assert call.get("force") is True
    assert call.get("use_cache") is False
    # since ≈ hw - 120s
    since_dt = datetime.fromisoformat(str(call["since"]).replace("Z", "+00:00"))
    expected = hw.timestamp() - OVERLAP_SECONDS
    assert abs(since_dt.timestamp() - expected) < 2.0

    db_session.expire_all()
    n = (
        db_session.query(MailItem)
        .filter(MailItem.account_id == account_id, MailItem.folder == "inbox")
        .count()
    )
    assert n == 1
