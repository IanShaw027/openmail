"""SMTP send + IMAP APPEND to Sent (mocked)."""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from app.services.send_service import append_to_sent_imap, send_via_smtp


def test_append_to_sent_imap_success() -> None:
    mock_conn = MagicMock()
    mock_conn.append.return_value = ("OK", [b"APPEND completed"])

    provider = MagicMock()
    provider._login_connect.return_value = mock_conn
    provider._select_folder.return_value = ("Sent", None)

    msg = EmailMessage()
    msg["From"] = "me@example.com"
    msg["To"] = "you@example.com"
    msg["Subject"] = "hi"
    msg.set_content("body")

    with patch("app.services.send_service.ImapProvider", return_value=provider):
        with patch("app.providers.imap_hosts.resolve_imap_host") as rh:
            rh.return_value = MagicMock(host="imap.example.com", port=993, ssl=True)
            ok, err = append_to_sent_imap(
                email_addr="me@example.com",
                password="secret",
                raw_message=msg,
                imap_host="imap.example.com",
            )
    assert ok is True
    assert err is None
    mock_conn.append.assert_called_once()
    args = mock_conn.append.call_args[0]
    assert args[0] == '"Sent"'
    mock_conn.logout.assert_called()


def test_append_to_sent_soft_fails_without_folder() -> None:
    mock_conn = MagicMock()
    provider = MagicMock()
    provider._login_connect.return_value = mock_conn
    provider._select_folder.return_value = (None, "no sent")

    with patch("app.services.send_service.ImapProvider", return_value=provider):
        with patch("app.providers.imap_hosts.resolve_imap_host") as rh:
            rh.return_value = MagicMock(host="imap.example.com", port=993, ssl=True)
            ok, err = append_to_sent_imap(
                email_addr="me@example.com",
                password="secret",
                raw_message=b"raw",
            )
    assert ok is False
    assert err
    mock_conn.append.assert_not_called()


def test_send_via_smtp_appends_after_success() -> None:
    class _Smtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            return None

        def starttls(self, context=None):
            return None

        def login(self, u, p):
            return None

        def send_message(self, msg):
            return None

    with (
        patch("app.services.send_service.resolve_smtp_host") as rs,
        patch("app.services.ssrf.resolve_mail_endpoint", return_value=("1.2.3.4", 587, "smtp.example.com")),
        patch("smtplib.SMTP", return_value=_Smtp()),
        patch("app.services.send_service.append_to_sent_imap", return_value=(True, None)) as ap,
    ):
        rs.return_value = MagicMock(
            host="smtp.example.com", port=587, use_starttls=True, use_ssl=False
        )
        result = send_via_smtp(
            email_addr="me@example.com",
            password="secret",
            to=["you@example.com"],
            subject="s",
            body_text="b",
            imap_host="imap.example.com",
            save_to_sent=True,
        )
    assert result.ok is True
    assert result.saved_to_sent is True
    assert "saved to Sent" in (result.detail or "")
    ap.assert_called_once()


def test_send_via_smtp_still_ok_if_append_fails() -> None:
    class _Smtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            return None

        def starttls(self, context=None):
            return None

        def login(self, u, p):
            return None

        def send_message(self, msg):
            return None

    with (
        patch("app.services.send_service.resolve_smtp_host") as rs,
        patch("app.services.ssrf.resolve_mail_endpoint", return_value=("1.2.3.4", 587, "smtp.example.com")),
        patch("smtplib.SMTP", return_value=_Smtp()),
        patch(
            "app.services.send_service.append_to_sent_imap",
            return_value=(False, "no sent"),
        ),
    ):
        rs.return_value = MagicMock(
            host="smtp.example.com", port=587, use_starttls=True, use_ssl=False
        )
        result = send_via_smtp(
            email_addr="me@example.com",
            password="secret",
            to=["you@example.com"],
            subject="s",
            body_text="b",
            save_to_sent=True,
        )
    assert result.ok is True
    assert result.saved_to_sent is False
    assert "APPEND skipped" in (result.detail or "")


def test_send_via_smtp_tunnels_through_proxy() -> None:
    class _Smtp:
        def __init__(self, *a, **k):
            self.kwargs = k

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            return None

        def send_message(self, msg):
            return None

    fake_sock = object()
    captured: dict[str, object] = {}

    def fake_ssl(*a, **k):
        captured["kwargs"] = k
        return _Smtp(*a, **k)

    with (
        patch("app.services.send_service.resolve_smtp_host") as rs,
        patch(
            "app.services.ssrf.resolve_mail_endpoint",
            return_value=("1.2.3.4", 465, "smtp.example.com"),
        ),
        patch("app.services.tcp_proxy.open_proxied_tcp", return_value=fake_sock) as op,
        patch("app.services.send_service._SMTP_SSL", side_effect=fake_ssl),
        patch("app.services.send_service.append_to_sent_imap", return_value=(True, None)),
    ):
        rs.return_value = MagicMock(
            host="smtp.example.com", port=465, use_starttls=False, use_ssl=True
        )
        result = send_via_smtp(
            email_addr="me@example.com",
            password="secret",
            to=["you@example.com"],
            subject="s",
            body_text="b",
            proxy="socks5://127.0.0.1:1080",
            save_to_sent=False,
        )
    assert result.ok is True
    op.assert_called_once()
    assert op.call_args[0][0] == "socks5://127.0.0.1:1080"
    assert captured["kwargs"].get("sock") is fake_sock
