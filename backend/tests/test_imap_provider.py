"""IMAP provider tests: host table, MIME parse, mocked connection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import resolve_provider
from app.providers.imap_hosts import DOMAIN_IMAP_HOSTS, resolve_imap_host
from app.providers.imap_provider import ImapProvider, parse_rfc822


def test_imap_utf7_encode_chinese_mailbox() -> None:
    from app.providers.imap_provider import _imap_utf7_encode, _mailbox_select_variants

    # Pure ASCII unchanged
    assert _imap_utf7_encode("Sent") == "Sent"
    # Non-ASCII → modified UTF-7 (starts with &)
    enc = _imap_utf7_encode("已发送")
    assert enc.isascii()
    assert enc.startswith("&") or "&" in enc
    variants = _mailbox_select_variants("已发送")
    assert "已发送" in variants
    assert any(v.isascii() and v != "已发送" for v in variants)


def test_imap_ssl_connect_uses_sni_hostname_not_ip() -> None:
    """Pinned IP TCP peer must still verify cert as original hostname (Gmail)."""
    from app.providers.imap_provider import _IMAP4SSLSni

    captured: dict[str, str] = {}

    class FakeCtx:
        def wrap_socket(self, sock, server_hostname=None):  # type: ignore[no-untyped-def]
            captured["server_hostname"] = str(server_hostname or "")
            return sock

    inst = object.__new__(_IMAP4SSLSni)
    inst._tls_server_hostname = "imap.gmail.com"
    inst.ssl_context = FakeCtx()  # type: ignore[assignment]
    with patch("imaplib.IMAP4._create_socket", return_value=object()) as tcp:
        sock = inst._create_socket(30)
    tcp.assert_called_once()
    assert sock is not None
    assert captured.get("server_hostname") == "imap.gmail.com"


def test_domain_host_table() -> None:
    assert resolve_imap_host("a@qq.com").host == "imap.qq.com"
    assert resolve_imap_host("a@163.com").host == "imap.163.com"
    assert resolve_imap_host("a@126.com").host == "imap.126.com"
    assert resolve_imap_host("a@gmail.com").host == "imap.gmail.com"
    assert resolve_imap_host("a@outlook.com").host == "outlook.office365.com"
    assert resolve_imap_host("a@hotmail.com").port == 993
    assert resolve_imap_host("a@icloud.com").host == "imap.mail.me.com"


def test_explicit_host_overrides_domain() -> None:
    with patch("app.services.ssrf.pick_safe_ip", return_value="93.184.216.34"):
        h = resolve_imap_host(
            "a@gmail.com", imap_host="custom.example", imap_port=143, imap_ssl=False
        )
    assert h.host == "custom.example"
    assert h.port == 143
    assert h.ssl is False


def test_unknown_domain_raises() -> None:
    with pytest.raises(ValueError, match="IMAP"):
        resolve_imap_host("user@totally-unknown-xyz.example")


def test_parse_rfc822_multipart_and_code() -> None:
    raw = b"""From: Auth <auth@example.com>\r
To: user@qq.com\r
Subject: =?UTF-8?B?6aqM6K+B56CBIDEyMzQ1Ng==?=\r
Date: Mon, 1 Aug 2026 12:00:00 +0000\r
MIME-Version: 1.0\r
Content-Type: multipart/alternative; boundary="bnd"\r
\r
--bnd\r
Content-Type: text/plain; charset=utf-8\r
\r
Your verification code is 123456\r
--bnd\r
Content-Type: text/html; charset=utf-8\r
\r
<p>Your verification code is <b>123456</b></p>\r
--bnd--\r
"""
    msg = parse_rfc822(raw, msg_id="42", folder="inbox")
    assert msg.id == "42"
    assert "123456" in msg.subject or msg.verification_code == "123456"
    assert msg.from_address == "auth@example.com"
    assert "123456" in (msg.body_text or "")
    assert msg.verification_code == "123456"
    assert msg.folder == "inbox"


def test_parse_rfc822_gb18030_subject() -> None:
    # Subject: 你好 (UTF-8 encoded-word)
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: =?UTF-8?B?5L2g5aW9?=\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"hello\r\n"
    )
    msg = parse_rfc822(raw, msg_id="1")
    assert msg.subject == "你好"
    assert msg.body_text.strip() == "hello"


def test_resolve_provider_imap() -> None:
    acc = SimpleNamespace(provider="imap", email="u@qq.com")
    p = resolve_provider(acc)
    assert p is not None
    assert p.name == "imap"
    assert isinstance(p, ImapProvider)


def test_fetch_missing_password() -> None:
    acc = SimpleNamespace(provider="imap", email="u@qq.com")
    r = ImapProvider().fetch(acc, credentials={})
    assert r.ok is False
    assert "密码" in (r.error or "")


def test_fetch_with_mocked_imap() -> None:
    raw = (
        b"From: s@e.com\r\nSubject: code 555666\r\n"
        b"Content-Type: text/plain\r\n\r\nYour OTP 555666\r\n"
    )
    mock_conn = MagicMock()
    mock_conn.login.return_value = ("OK", [b"Logged in"])
    mock_conn.select.return_value = ("OK", [b"2"])
    mock_conn.uid.side_effect = [
        ("OK", [b"10 11"]),  # search
        ("OK", [(b"11 (RFC822 {n})", raw)]),  # fetch first (newest reversed)
        ("OK", [(b"10 (RFC822 {n})", raw)]),
    ]
    mock_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" INBOX'])
    mock_conn.logout.return_value = ("BYE", [])

    acc = SimpleNamespace(provider="imap", email="u@qq.com")
    provider = ImapProvider(timeout=5)

    with patch.object(provider, "_connect", return_value=mock_conn):
        result = provider.fetch(
            acc,
            quick=True,
            credentials={"password": "app-pass", "imap_host": "imap.qq.com"},
        )

    assert result.ok is True
    assert result.message_count >= 1
    assert result.messages[0].verification_code == "555666"
    mock_conn.login.assert_called_once()
    mock_conn.logout.assert_called()


def test_domain_table_keys_present() -> None:
    for d in ("qq.com", "163.com", "126.com", "gmail.com", "outlook.com"):
        assert d in DOMAIN_IMAP_HOSTS
