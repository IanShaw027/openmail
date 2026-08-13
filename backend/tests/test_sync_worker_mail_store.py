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


def test_sync_folder_full_page_all_known_pages_older(db_session: Session) -> None:
    """Full page of already-stored mail must NOT stop catch-up (review H1).

    Newest-first: first page all known → second page uses before=oldest.
    """
    from app.services.mail_store import upsert_messages
    from app.services.sync_worker import PAGE_CATCHUP

    acc = _make_account(db_session, email="overlap@example.com")
    account_id = acc.id

    # Pre-seed PAGE_CATCHUP "known" messages (newest block)
    known = [
        Message(
            id=f"known-{i}",
            subject=f"Known {i}",
            from_="k@e.com",
            date=f"2026-08-01T12:{i:02d}:00+00:00",
            body_text=f"body {i}",
        )
        for i in range(PAGE_CATCHUP)
    ]
    upsert_messages(db_session, account_id, "inbox", known)
    db_session.commit()

    calls: list[dict] = []

    def _fake_fetch(db, account, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(dict(kwargs))
        folder = str(kwargs.get("folder", "inbox")).lower()
        if folder != "inbox":
            return FetchServiceResult(ok=True, messages=[], message_count=0, folder=folder)
        before = kwargs.get("before")
        if before is None:
            # First page: full page of already-known (newest)
            return FetchServiceResult(
                ok=True,
                messages=known,
                message_count=len(known),
                folder=folder,
                account_id=account.id,
                email=account.email,
            )
        # Second page: older unseen mail
        older = Message(
            id="older-1",
            subject="Older unseen",
            from_="o@e.com",
            date="2026-07-01T10:00:00+00:00",
            body_text="should not be skipped",
        )
        return FetchServiceResult(
            ok=True,
            messages=[older],
            message_count=1,
            folder=folder,
            account_id=account.id,
            email=account.email,
        )

    with patch("app.services.sync_worker.fetch_account", side_effect=_fake_fetch):
        worker = get_sync_worker()
        detail = worker.sync_one_account(account_id, force=True)

    assert detail["ok"] is True
    inbox_calls = [c for c in calls if str(c.get("folder", "")).lower() == "inbox"]
    assert len(inbox_calls) >= 2, f"expected multi-page, got {inbox_calls}"
    assert inbox_calls[0].get("before") is None
    assert inbox_calls[1].get("before") is not None

    db_session.expire_all()
    sids = {
        r.stable_id
        for r in db_session.query(MailItem)
        .filter(MailItem.account_id == account_id, MailItem.folder == "inbox")
        .all()
    }
    assert "p:older-1" in sids


def test_sync_folder_page2_failure_does_not_advance_high_water(db_session: Session) -> None:
    """Newest-first: a failed later page must not raise the time cursor to page-1 newest.

    Otherwise mail still inside the since-window is skipped forever on the next poll.
    """
    from app.services.mail_store import save_cursor_time_high_water
    from app.services.sync_worker import PAGE_CATCHUP

    acc = _make_account(db_session, email="hw-fail@example.com")
    account_id = acc.id
    old_hw = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    save_cursor_time_high_water(
        db_session, account_id, "inbox", old_hw, high_water_ids=["p:old"]
    )
    db_session.commit()

    page1 = [
        Message(
            id=f"new-{i}",
            subject=f"New {i}",
            from_="n@e.com",
            date=f"2026-08-01T12:{i:02d}:00+00:00",
            body_text=f"body {i}",
        )
        for i in range(PAGE_CATCHUP)
    ]

    def _fake_fetch(db, account, **kwargs):  # type: ignore[no-untyped-def]
        folder = str(kwargs.get("folder", "inbox")).lower()
        if folder != "inbox":
            return FetchServiceResult(ok=True, messages=[], message_count=0, folder=folder)
        if kwargs.get("before") is None:
            return FetchServiceResult(
                ok=True,
                messages=page1,
                message_count=len(page1),
                folder=folder,
                account_id=account.id,
                email=account.email,
            )
        return FetchServiceResult(
            ok=False,
            error="upstream timeout",
            messages=[],
            message_count=0,
            folder=folder,
        )

    with patch("app.services.sync_worker.fetch_account", side_effect=_fake_fetch):
        worker = get_sync_worker()
        worker.sync_one_account(account_id, force=True)

    db_session.expire_all()
    cur = (
        db_session.query(SyncCursor)
        .filter(SyncCursor.account_id == account_id, SyncCursor.folder == "inbox")
        .one()
    )
    data = json.loads(cur.cursor_json or "{}")
    assert "2026-06-01T00:00:00" in data["high_water_time"]
    assert "p:old" in (data.get("high_water_ids") or [])
    assert "2026-08-01" not in data["high_water_time"]


def test_weak_stable_id_matches_frontend_material() -> None:
    """Server weak id is wh_ + sha256[:40] of from|date|subject|size."""
    from app.services.mail_store import compute_stable_id
    import hashlib

    class M:
        id = None
        from_addr = "Alice <a@b.com>"
        date = "2026-08-01T12:00:00Z"
        subject = "Hello"
        size = None

    sid = compute_stable_id(M())
    material = "Alice <a@b.com>|2026-08-01T12:00:00Z|Hello|"
    expect = "wh_" + hashlib.sha256(material.encode()).hexdigest()[:40]
    assert sid == expect
