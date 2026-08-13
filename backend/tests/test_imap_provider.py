"""IMAP provider tests: host table, MIME parse, mocked connection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import imaplib
import pytest

from app.providers.base import resolve_provider
from app.providers.imap_hosts import DOMAIN_IMAP_HOSTS, resolve_imap_host
from app.providers.imap_provider import ImapProvider, parse_rfc822


def test_imap_utf7_encode_chinese_mailbox() -> None:
    from app.providers.imap_provider import _imap_utf7_encode, _imap_utf7_decode, _mailbox_select_variants

    assert _imap_utf7_encode("Sent") == "Sent"
    assert _imap_utf7_encode("R&D") == "R&-D"
    assert _imap_utf7_decode("R&-D") == "R&D"
    enc = _imap_utf7_encode("已发送")
    assert enc.isascii()
    assert enc.startswith("&") or "&" in enc
    assert _imap_utf7_decode(enc) == "已发送"
    variants = _mailbox_select_variants("已发送")
    assert "已发送" in variants
    assert any(v.isascii() and v != "已发送" for v in variants)


def test_safe_uids_reject_wildcards_and_crlf() -> None:
    from app.providers.imap_provider import _safe_uids

    assert _safe_uids([b"1", b"2", b"1:*", b"3\r\nSTORE 1:* +FLAGS (\\Deleted)"]) == ["1", "2"]
    assert _safe_uids(["10", "abc", ""]) == ["10"]


def test_imap_non_ssl_requires_starttls_before_login() -> None:
    """ssl=false means STARTTLS on 143, never plaintext LOGIN."""
    mock_conn = MagicMock()
    mock_conn.login.return_value = ("OK", [b"Logged in"])
    mock_conn.starttls.return_value = ("OK", [])

    provider = ImapProvider()
    fake_imap4 = MagicMock(return_value=mock_conn)
    fake_imap4.error = imaplib.IMAP4.error
    with (
        patch(
            "app.services.ssrf.resolve_mail_endpoint",
            return_value=("93.184.216.34", 143, "imap.example.com"),
        ),
        patch("app.providers.imap_provider.imaplib.IMAP4", fake_imap4),
    ):
        conn = provider._login_connect(
            "imap.example.com", 143, False, "u@example.com", "secret"
        )

    fake_imap4.assert_called_once()
    assert conn is mock_conn
    names = [c[0] for c in mock_conn.method_calls]
    assert "starttls" in names
    assert names.index("starttls") < names.index("login")


def test_imap_non_ssl_aborts_login_if_starttls_fails() -> None:
    mock_conn = MagicMock()
    mock_conn.starttls.side_effect = OSError("STARTTLS failed")

    provider = ImapProvider()
    fake_imap4 = MagicMock(return_value=mock_conn)
    fake_imap4.error = imaplib.IMAP4.error
    with (
        patch(
            "app.services.ssrf.resolve_mail_endpoint",
            return_value=("93.184.216.34", 143, "imap.example.com"),
        ),
        patch("app.providers.imap_provider.imaplib.IMAP4", fake_imap4),
        pytest.raises(RuntimeError, match="STARTTLS"),
    ):
        provider._login_connect(
            "imap.example.com", 143, False, "u@example.com", "secret"
        )

    mock_conn.login.assert_not_called()


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
    assert "123456" in (msg.body_html or "")
    assert msg.verification_code == "123456"
    assert msg.folder == "inbox"


def test_parse_rfc822_nested_mixed_related_html() -> None:
    """QQ/163-style: multipart/mixed → alternative → related + html."""
    raw = b"""From: "QQ" <noreply@qq.com>\r
To: user@qq.com\r
Subject: code 998877\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary="mix"\r
\r
--mix\r
Content-Type: multipart/alternative; boundary="alt"\r
\r
--alt\r
Content-Type: text/plain; charset=utf-8\r
\r
plain only stub\r
--alt\r
Content-Type: multipart/related; boundary="rel"\r
\r
--rel\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><p>Your QQ code is <b>998877</b></p></body></html>\r
--rel--\r
--alt--\r
--mix\r
Content-Type: application/octet-stream; name="x.bin"\r
Content-Disposition: attachment; filename="x.bin"\r
\r
ATTACH\r
--mix--\r
"""
    msg = parse_rfc822(raw, msg_id="qq1", folder="inbox")
    assert "998877" in (msg.body_html or "")
    assert msg.verification_code == "998877"
    assert "998877" in (msg.body_text or "")


def test_parse_rfc822_html_only_part() -> None:
    raw = (
        b"From: a@b.com\r\nSubject: Hi\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><div>Hello <b>world</b></div></body></html>\r\n"
    )
    msg = parse_rfc822(raw, msg_id="h1")
    assert "Hello" in (msg.body_html or "")
    assert "Hello" in (msg.body_text or "")
    assert "world" in (msg.body_text or "")


def test_parse_rfc822_mislabelled_html_as_plain() -> None:
    raw = (
        b"From: a@b.com\r\nSubject: otp\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"<html><body><p>code <b>112233</b></p></body></html>\r\n"
    )
    msg = parse_rfc822(raw, msg_id="m1")
    assert msg.body_html
    assert "112233" in (msg.body_html or "")
    assert msg.verification_code == "112233"


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
    mock_conn.capabilities = ("IMAP4rev1", "ID")
    mock_conn._simple_command.return_value = ("OK", [b"ID completed"])
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
    # Client ID sent after login when CAPABILITY includes ID
    mock_conn._simple_command.assert_called()
    id_calls = [c for c in mock_conn._simple_command.call_args_list if c.args and c.args[0] == "ID"]
    assert id_calls, "expected IMAP ID after login"


def test_netease_select_requires_client_id() -> None:
    """NetEase returns SELECT NO Unsafe Login without ID — surface clear error."""
    mock_conn = MagicMock()
    mock_conn.login.return_value = ("OK", [b"Logged in"])
    mock_conn.capabilities = ("IMAP4rev1", "ID")
    mock_conn._simple_command.return_value = ("OK", [b"ID completed"])
    mock_conn.select.return_value = (
        "NO",
        [b"EXAMINE Unsafe Login. Please contact kefu@188.com for help"],
    )
    mock_conn.list.return_value = ("OK", [b'() "/" "INBOX"'])
    mock_conn.logout.return_value = ("BYE", [])

    provider = ImapProvider(timeout=5)
    with patch.object(provider, "_connect", return_value=mock_conn):
        result = provider.fetch(
            SimpleNamespace(provider="imap", email="u@126.com"),
            folder="inbox",
            quick=True,
            credentials={"password": "auth-code", "imap_host": "imap.126.com"},
        )
    assert result.ok is False
    assert "Unsafe" in (result.error or "") or "网易" in (result.error or "")


def test_send_client_id_forced_for_netease_host() -> None:
    provider = ImapProvider(timeout=5)
    conn = MagicMock()
    conn.capabilities = ()  # empty
    conn.capability.return_value = ("OK", [b"IMAP4rev1"])
    conn._simple_command.return_value = ("OK", [b"ID completed"])
    provider._send_client_id(conn, host="imap.126.com")
    conn._simple_command.assert_called()
    assert conn._simple_command.call_args.args[0] == "ID"


def test_before_paging_filters_exact_time_within_same_day() -> None:
    def raw(date: str, subject: str) -> bytes:
        return (
            f"From: sender@example.com\r\nDate: {date}\r\nSubject: {subject}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nbody\r\n"
        ).encode()

    provider = ImapProvider(timeout=5)
    conn = MagicMock()
    conn.login.return_value = ("OK", [b"Logged in"])
    conn.select.return_value = ("OK", [b"3"])
    conn.response.return_value = (None, None)
    conn.logout.return_value = ("BYE", [])

    payloads = {
        "3": raw("Mon, 03 Aug 2026 15:00:00 +0000", "newer"),
        "2": raw("Mon, 03 Aug 2026 11:00:00 +0000", "older one"),
        "1": raw("Mon, 03 Aug 2026 10:00:00 +0000", "older two"),
    }
    with (
        patch.object(provider, "_login_connect", return_value=conn),
        patch.object(provider, "_uids_before", return_value=["3", "2", "1"]),
        patch.object(provider, "_fetch_rfc822", side_effect=lambda _conn, uid: payloads[uid]),
    ):
        result = provider.fetch(
                SimpleNamespace(provider="imap", email="u@qq.com"),
            folder="inbox",
            quick=True,
            limits={"before": "2026-08-03T12:00:00Z", "max_messages": 2},
                credentials={"password": "secret", "imap_host": "imap.qq.com"},
        )

    assert result.ok is True
    assert [message.subject for message in result.messages] == ["older one", "older two"]


def test_imap_fetch_passes_credential_proxy() -> None:
    provider = ImapProvider()
    captured: dict[str, object] = {}

    def fake_login(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["args"] = args
        captured["kwargs"] = kwargs
        raise OSError("stop after capturing proxy")

    with (
        patch.object(provider, "_login_connect", side_effect=fake_login),
        patch(
            "app.providers.imap_provider.resolve_imap_host",
            return_value=SimpleNamespace(host="imap.example.com", port=993, ssl=True),
        ),
    ):
        result = provider.fetch(
            SimpleNamespace(provider="imap", email="u@example.com", proxy=None),
            credentials={
                "password": "secret",
                "proxy": "socks5://10.0.0.1:1080",
            },
        )
    assert result.ok is False
    assert captured.get("kwargs", {}).get("proxy") == "socks5://10.0.0.1:1080"  # type: ignore[union-attr]


def test_imap_connect_opens_proxied_socket() -> None:
    fake_sock = object()
    with (
        patch(
            "app.services.ssrf.resolve_mail_endpoint",
            return_value=("93.184.216.34", 993, "imap.example.com"),
        ),
        patch("app.services.tcp_proxy.open_proxied_tcp", return_value=fake_sock) as op,
        patch("app.providers.imap_provider._imap4_ssl_with_sni") as ssl_fn,
    ):
        ssl_fn.return_value = MagicMock()
        ImapProvider()._connect(
            "imap.example.com",
            993,
            True,
            proxy="socks5://127.0.0.1:1080",
        )
    op.assert_called_once()
    assert op.call_args[0][0] == "socks5://127.0.0.1:1080"
    assert op.call_args[0][1] == "93.184.216.34"
    assert op.call_args[0][2] == 993
    assert ssl_fn.call_args.kwargs.get("sock") is fake_sock or (
        "sock" in ssl_fn.call_args.kwargs
    )


def test_domain_table_keys_present() -> None:
    for d in ("qq.com", "163.com", "126.com", "gmail.com", "outlook.com"):
        assert d in DOMAIN_IMAP_HOSTS
