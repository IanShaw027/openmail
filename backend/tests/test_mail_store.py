"""Unit tests for mail_store: stable_id, upsert dedupe, delta keyset pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from app.config import get_settings
from app.models import Account, AccountPool, AccountStatus, MailItem, ProviderType, SyncCursor
from app.providers.base import Message
from app.services.mail_store import (
    compute_stable_id,
    get_or_create_cursor,
    list_delta,
    message_to_fields,
    normalize_folder,
    save_cursor_time_high_water,
    upsert_messages,
)


def _make_account(db, *, owner: str = "vk_device_mail_store", email: str = "a@example.com") -> Account:
    acc = Account(
        email=email,
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
        owner_user_id=owner,
        status=AccountStatus.ok,
        sync_enabled=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def test_normalize_folder():
    assert normalize_folder("INBOX") == "inbox"
    assert normalize_folder("junk") == "spam"
    assert normalize_folder("Junk Email") == "spam"
    assert normalize_folder("spam") == "spam"
    assert normalize_folder("Sent Items") == "sent"
    assert normalize_folder(None) == "inbox"
    assert normalize_folder("") == "inbox"


def test_stable_id_provider_priority():
    # provider id from Message.id
    m1 = Message(id="graph-abc", subject="Hi", from_="x@y.com", date="2026-01-01T00:00:00Z")
    sid1 = compute_stable_id(m1)
    assert sid1 == "p:graph-abc"
    assert compute_stable_id(m1) == sid1  # stable

    # IMAP uidvalidity:uid
    m2 = Message(id="42", subject="Hi", uidvalidity=99)
    assert compute_stable_id(m2) == "p:99:42"

    # Numeric IMAP UID without UIDVALIDITY is not stable across mailbox rebuilds.
    m_bare = Message(id="42", subject="Hi", from_="x@y.com", date="2026-01-01T00:00:00Z")
    bare_sid = compute_stable_id(m_bare)
    assert bare_sid != "p:42"
    assert not bare_sid.startswith("p:42")
    assert bare_sid.startswith("wh_")

    # message_id when no strong provider id (empty id)
    m3 = {
        "id": "",
        "message_id": "<ABC@mail.example>",
        "subject": "Code",
        "from": "a@b.com",
        "date": "2026-01-02T00:00:00Z",
    }
    sid3 = compute_stable_id(m3)
    assert sid3.startswith("m:")
    assert "abc@mail.example" in sid3
    assert compute_stable_id(m3) == sid3

    # weak fingerprint when nothing else
    m4 = {"subject": "S", "from": "f@e.com", "date": "2020-01-01", "size": 10}
    sid4 = compute_stable_id(m4)
    assert sid4.startswith("wh_")
    assert compute_stable_id(m4) == sid4
    # different size → different fingerprint
    assert compute_stable_id({**m4, "size": 11}) != sid4


def test_message_to_fields_from_message_dataclass():
    m = Message(
        id="mid-1",
        subject="Your code 123456",
        from_="noreply@svc.com",
        from_address="noreply@svc.com",
        to="user@example.com",
        date="2026-03-01T12:00:00+00:00",
        body_preview="code is 123456",
        body_text="Your code is 123456",
        body_html="<p>123456</p>",
        verification_code="123456",
    )
    f = message_to_fields(m)
    assert f["stable_id"] == "p:mid-1"
    assert f["from_addr"] == "noreply@svc.com"
    assert f["subject"] == "Your code 123456"
    assert f["verification_code"] == "123456"
    assert f["body_text"] == "Your code is 123456"
    assert f["received_at"] is not None
    assert f["received_at"].tzinfo is not None


def test_upsert_insert_then_unchanged(db_session):
    settings = get_settings()
    acc = _make_account(db_session)
    msgs = [
        Message(
            id="p1",
            subject="Hello",
            from_="a@b.com",
            date="2026-01-01T00:00:00Z",
            body_text="body one",
            body_preview="body one",
            verification_code="111111",
        )
    ]
    r1 = upsert_messages(db_session, acc.id, "inbox", msgs, settings=settings)
    db_session.commit()
    assert r1 == {"inserted": 1, "updated": 0, "unchanged": 0}

    n = db_session.query(MailItem).filter(MailItem.account_id == acc.id).count()
    assert n == 1
    row = db_session.query(MailItem).one()
    assert row.stable_id == "p:p1"
    assert row.subject == "Hello"
    assert row.verification_code != "111111"
    assert row.preview != "body one"
    assert row.body_text_enc  # encrypted
    assert row.body_text_enc != "body one"
    updated_at_1 = row.updated_at

    r2 = upsert_messages(db_session, acc.id, "inbox", msgs, settings=settings)
    db_session.commit()
    assert r2 == {"inserted": 0, "updated": 0, "unchanged": 1}
    row2 = db_session.query(MailItem).one()
    assert row2.updated_at == updated_at_1


def test_upsert_dedupe_and_content_update(db_session):
    settings = get_settings()
    acc = _make_account(db_session)
    m = Message(
        id="same",
        subject="v1",
        from_="a@b.com",
        date="2026-02-01T00:00:00Z",
        body_text="t1",
    )
    upsert_messages(db_session, acc.id, "INBOX", [m], settings=settings)
    db_session.commit()

    m2 = Message(
        id="same",
        subject="v2",
        from_="a@b.com",
        date="2026-02-01T00:00:00Z",
        body_text="t2",
        verification_code="999999",
    )
    r = upsert_messages(db_session, acc.id, "inbox", [m2], settings=settings)
    db_session.commit()
    assert r["updated"] == 1
    assert r["inserted"] == 0

    rows = db_session.query(MailItem).filter(MailItem.account_id == acc.id).all()
    assert len(rows) == 1
    assert rows[0].subject == "v2"
    assert rows[0].verification_code != "999999"
    page = list_delta(db_session, acc.owner_user_id, since=None, limit=10, settings=settings)
    assert page["mails"][0]["verification_code"] == "999999"


def test_upsert_junk_maps_to_spam_folder(db_session):
    settings = get_settings()
    acc = _make_account(db_session)
    m = Message(id="j1", subject="spammy", from_="x@y.z")
    upsert_messages(db_session, acc.id, "junk", [m], settings=settings)
    db_session.commit()
    row = db_session.query(MailItem).one()
    assert row.folder == "spam"


def test_delta_keyset_pagination(db_session):
    settings = get_settings()
    device = "vk_delta_device_01"
    acc = _make_account(db_session, owner=device, email="delta@example.com")
    # other device account must not appear
    other = _make_account(db_session, owner="vk_other", email="other@example.com")

    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    msgs = []
    for i in range(5):
        msgs.append(
            Message(
                id=f"d{i}",
                subject=f"s{i}",
                from_="n@e.com",
                date=(base + timedelta(minutes=i)).isoformat(),
                body_preview=f"p{i}",
            )
        )
    upsert_messages(db_session, acc.id, "inbox", msgs, settings=settings)
    upsert_messages(
        db_session,
        other.id,
        "inbox",
        [Message(id="secret", subject="nope", from_="z@z.z")],
        settings=settings,
    )
    db_session.commit()

    # Force distinct updated_at for keyset (upsert used same now for batch)
    rows = (
        db_session.query(MailItem)
        .filter(MailItem.account_id == acc.id)
        .order_by(MailItem.stable_id.asc())
        .all()
    )
    for i, row in enumerate(rows):
        row.updated_at = base + timedelta(seconds=i)
    db_session.commit()

    page1 = list_delta(db_session, device, since=None, limit=2, settings=settings)
    assert len(page1["mails"]) == 2
    assert page1["has_more"] is True
    assert page1["server_time"]
    assert all(m["email"] == "delta@example.com" for m in page1["mails"])
    assert all(m["body_text"] is None for m in page1["mails"])  # no body_text on these msgs
    page1_plain = list_delta(
        db_session, device, since=None, limit=2, include_body=False, settings=settings
    )
    assert all(m["body_text"] is None for m in page1_plain["mails"])

    last = page1["mails"][-1]
    page2 = list_delta(
        db_session,
        device,
        since=last["updated_at"],
        since_id=last["id"],
        limit=2,
        settings=settings,
    )
    assert len(page2["mails"]) == 2
    assert page2["has_more"] is True
    # no overlap with page1
    ids1 = {m["id"] for m in page1["mails"]}
    ids2 = {m["id"] for m in page2["mails"]}
    assert ids1.isdisjoint(ids2)

    last2 = page2["mails"][-1]
    page3 = list_delta(
        db_session,
        device,
        since=last2["updated_at"],
        since_id=last2["id"],
        limit=2,
        settings=settings,
    )
    assert len(page3["mails"]) == 1
    assert page3["has_more"] is False

    all_ids = ids1 | ids2 | {m["id"] for m in page3["mails"]}
    assert len(all_ids) == 5


def test_upsert_encrypts_preview_and_code_delta_returns_plaintext(db_session):
    settings = get_settings()
    acc = _make_account(db_session, owner="vk_enc_preview", email="enc@example.com")
    msgs = [
        Message(
            id="enc1",
            subject="OTP 888777",
            from_="noreply@svc.com",
            from_address="noreply@svc.com",
            date="2026-04-01T00:00:00Z",
            body_preview="your code is 888777 please enter",
            body_text="Your code is 888777",
            body_html="<p>888777</p>",
            verification_code="888777",
        )
    ]
    upsert_messages(db_session, acc.id, "inbox", msgs, settings=settings)
    db_session.commit()

    row = db_session.query(MailItem).one()
    assert row.subject == "OTP 888777"
    assert row.from_addr == "noreply@svc.com"
    assert row.preview != "your code is 888777 please enter"
    assert row.verification_code != "888777"
    assert "888777" not in (row.preview or "")
    assert "888777" not in (row.verification_code or "")

    from app.crypto import decrypt_str

    assert decrypt_str(row.preview, settings=settings) == "your code is 888777 please enter"
    assert decrypt_str(row.verification_code, settings=settings) == "888777"

    out = list_delta(db_session, acc.owner_user_id, since=None, limit=10, settings=settings)
    m = out["mails"][0]
    assert m["preview"] == "your code is 888777 please enter"
    assert m["verification_code"] == "888777"
    assert m["body_text"] == "Your code is 888777"
    assert m["body_html"] == "<p>888777</p>"

    omitted = list_delta(
        db_session,
        acc.owner_user_id,
        since=None,
        limit=10,
        include_body=False,
        settings=settings,
    )
    assert omitted["mails"][0]["body_text"] is None
    assert omitted["mails"][0]["body_html"] is None
    assert omitted["mails"][0]["preview"] == "your code is 888777 please enter"
    assert omitted["mails"][0]["verification_code"] == "888777"


def test_list_delta_reads_legacy_plaintext_preview_and_code(db_session):
    settings = get_settings()
    acc = _make_account(db_session, owner="vk_legacy_plain", email="legacy@example.com")
    row = MailItem(
        account_id=acc.id,
        folder="inbox",
        stable_id="p:legacy1",
        subject="Old code",
        from_addr="old@ex.com",
        preview="plain preview 112233",
        verification_code="112233",
    )
    db_session.add(row)
    db_session.commit()

    out = list_delta(db_session, acc.owner_user_id, since=None, limit=10, settings=settings)
    m = out["mails"][0]
    assert m["preview"] == "plain preview 112233"
    assert m["verification_code"] == "112233"


def test_upsert_rewrites_legacy_plaintext_when_content_unchanged(db_session):
    from app.crypto import decrypt_str
    from app.services.mail_store import compute_content_hash, message_to_fields

    settings = get_settings()
    acc = _make_account(db_session, owner="vk_plain_rewrite", email="rw@example.com")
    msg = Message(
        id="legacy1",
        subject="Old code",
        from_="old@ex.com",
        date="2026-04-01T00:00:00Z",
        body_preview="plain preview 112233",
        body_text="body",
        verification_code="112233",
    )
    fields = message_to_fields(msg)
    row = MailItem(
        account_id=acc.id,
        folder="inbox",
        stable_id=fields["stable_id"],
        content_hash=compute_content_hash(fields),
        subject=fields["subject"],
        from_addr=fields["from_addr"],
        preview="plain preview 112233",
        verification_code="112233",
    )
    db_session.add(row)
    db_session.commit()

    r = upsert_messages(db_session, acc.id, "inbox", [msg], settings=settings)
    db_session.commit()
    assert r["unchanged"] == 1
    row2 = db_session.query(MailItem).one()
    assert row2.verification_code != "112233"
    assert decrypt_str(row2.verification_code, settings=settings) == "112233"
    assert row2.preview != "plain preview 112233"
    assert decrypt_str(row2.preview, settings=settings) == "plain preview 112233"


def test_cursor_get_or_create_and_high_water(db_session):
    acc = _make_account(db_session)
    c1 = get_or_create_cursor(db_session, acc.id, "junk")
    db_session.commit()
    assert c1.folder == "spam"
    assert c1.mode == "time"

    c2 = get_or_create_cursor(db_session, acc.id, "spam")
    assert c2.id == c1.id

    save_cursor_time_high_water(
        db_session,
        acc.id,
        "spam",
        high_water_time="2026-05-01T00:00:00+00:00",
        high_water_ids=["p:a", "p:b"],
    )
    db_session.commit()
    row = db_session.query(SyncCursor).filter(SyncCursor.account_id == acc.id).one()
    assert "high_water_time" in (row.cursor_json or "")
    assert "p:a" in (row.cursor_json or "")


def test_models_import_cleanly():
    from app.models import MailItem, SyncCursor  # noqa: F401

    assert MailItem.__tablename__ == "mail_items"
    assert SyncCursor.__tablename__ == "sync_cursors"
