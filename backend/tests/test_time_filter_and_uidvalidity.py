"""Local time filter for cookie/http_api + Message uidvalidity field."""

from __future__ import annotations

from app.providers.base import Message, filter_messages_by_time
from app.schemas import SendMailRequest
from app.services.fetch_service import FetchServiceResult


def test_filter_since_keeps_newer():
    msgs = [
        Message(id="1", date="2026-01-01T00:00:00+00:00"),
        Message(id="2", date="2026-06-01T00:00:00+00:00"),
        Message(id="3", date="2026-08-01T00:00:00+00:00"),
    ]
    out = filter_messages_by_time(msgs, since="2026-05-01T00:00:00Z")
    ids = {m.id for m in out}
    assert "1" not in ids
    assert "2" in ids
    assert "3" in ids


def test_filter_before_keeps_older():
    msgs = [
        Message(id="1", date="2026-01-01T00:00:00+00:00"),
        Message(id="2", date="2026-06-01T00:00:00+00:00"),
        Message(id="3", date="2026-08-01T00:00:00+00:00"),
    ]
    out = filter_messages_by_time(msgs, before="2026-06-15T00:00:00Z")
    ids = {m.id for m in out}
    assert ids == {"1", "2"}


def test_filter_unparseable_date_kept_for_since():
    msgs = [
        Message(id="x", date="not-a-date"),
        Message(id="y", date="2026-01-01T00:00:00+00:00"),
    ]
    out = filter_messages_by_time(msgs, since="2026-06-01T00:00:00Z")
    assert any(m.id == "x" for m in out)
    assert not any(m.id == "y" for m in out)


def test_filter_supports_rfc2822_and_unix_timestamps():
    msgs = [
        Message(id="rfc", date="Sat, 01 Aug 2026 12:00:00 +0000"),
        Message(id="seconds", date="1785585600"),
        Message(id="milliseconds", date="1785585600000"),
    ]
    out = filter_messages_by_time(msgs, since="2026-08-01T11:00:00Z")
    assert {m.id for m in out} == {"rfc", "seconds", "milliseconds"}


def test_message_to_dict_includes_uidvalidity():
    m = Message(id="42", folder="inbox", uidvalidity=12345)
    d = m.to_dict()
    assert d["uidvalidity"] == 12345
    assert d["id"] == "42"


def test_fetch_service_result_carries_uidvalidity():
    r = FetchServiceResult(ok=True, uidvalidity=98765)
    assert r.uidvalidity == 98765
    r_default = FetchServiceResult(ok=True)
    assert r_default.uidvalidity is None


def test_send_mail_request_accepts_proxy():
    body = SendMailRequest(
        to=["a@example.com"],
        email="from@example.com",
        password="secret",
        proxy="socks5://127.0.0.1:1080",
    )
    assert body.proxy == "socks5://127.0.0.1:1080"
    body_none = SendMailRequest(to=["a@example.com"])
    assert body_none.proxy is None


def test_load_session_meta_and_build_credentials_injects_meta():
    """Stored AccountSession cookies + meta_enc reload into fetch credentials."""
    from types import SimpleNamespace

    from app.config import get_settings
    from app.crypto import encrypt_json
    from app.services.credentials import load_session_meta
    from app.services.fetch_service import _build_credentials_for_account

    s = get_settings()
    cookies = [{"name": "sid", "value": "abc"}]
    meta = {"site": "mail.com", "csrf": "tok-1"}
    account = SimpleNamespace(
        email="user@mail.com",
        credential_enc=None,
        password_enc=None,
        proxy=None,
        session=SimpleNamespace(
            cookies_enc=encrypt_json(cookies, settings=s),
            meta_enc=encrypt_json(meta, settings=s),
        ),
    )
    assert load_session_meta(account, settings=s) == meta
    creds = _build_credentials_for_account(account, settings=s)
    assert creds["cookies"] == cookies
    assert creds["session_meta"] == meta
    assert creds["email"] == "user@mail.com"
