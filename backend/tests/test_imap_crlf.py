"""IMAP mailbox / credential CRLF injection.

imaplib joins command arguments with spaces and appends CRLF with no quoting
of the mailbox name. A folder containing ``\\r\\n`` therefore emits a second
IMAP command in the same write — escaping the readonly EXAMINE semantics and
desynchronising the tagged-response stream. Credentials have the same hole
at LOGIN.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.providers.imap_provider import (
    ImapProvider,
    _has_control_chars,
    _quote_mailbox,
)


@pytest.mark.parametrize(
    "name",
    [
        "INBOX\r\nSTORE 1:* +FLAGS (\\Deleted)",
        "INBOX\nCLOSE",
        "INBOX\x00HIDDEN",
        "INBOX%*",
        "a" * 256,
        "",
    ],
)
def test_quote_mailbox_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        _quote_mailbox(name)


def test_quote_mailbox_escapes_quotes_and_backslashes() -> None:
    assert _quote_mailbox('foo"bar') == '"foo\\"bar"'
    assert _quote_mailbox("a\\b") == '"a\\\\b"'
    assert _quote_mailbox("INBOX") == '"INBOX"'


def test_select_folder_does_not_send_raw_crlf_to_the_socket() -> None:
    """The bytes on the wire must never contain an injected second command."""
    conn = MagicMock()
    sent: list[bytes] = []

    def select(mailbox, readonly=True):  # type: ignore[no-untyped-def]
        # Mirror imaplib: join args and terminate with CRLF.
        payload = b"EXAMINE " + (
            mailbox.encode("utf-8") if isinstance(mailbox, str) else mailbox
        ) + b"\r\n"
        sent.append(payload)
        return "NO", [b"invalid mailbox"]

    conn.select.side_effect = select

    provider = ImapProvider()
    selected, err = provider._select_folder(conn, "INBOX\r\nZ999 STORE 1:* +FLAGS (\\Deleted)")

    assert selected is None
    assert err
    # Either select was never called (rejected before send) or every payload
    # contains exactly one trailing CRLF — never an embedded one.
    for payload in sent:
        assert payload.count(b"\r\n") == 1
        assert b"STORE" not in payload


def test_login_rejects_credentials_with_control_chars() -> None:
    provider = ImapProvider()
    with pytest.raises(ValueError, match="control"):
        provider._login_connect("imap.example.com", 993, True, "a@b.com", "pw\r\nNOOP")
    with pytest.raises(ValueError, match="control"):
        provider._login_connect("imap.example.com", 993, True, "a\nb@c.com", "pw")


def test_has_control_chars_helper() -> None:
    assert _has_control_chars("ok", "also-ok") is False
    assert _has_control_chars("x\r", None) is True
    assert _has_control_chars(None, "y\n") is True
    assert _has_control_chars("z\x00") is True
