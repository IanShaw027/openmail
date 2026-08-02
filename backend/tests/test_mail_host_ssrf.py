"""IMAP/SMTP host SSRF guards."""

from __future__ import annotations

import pytest

from app.providers.imap_hosts import resolve_imap_host
from app.providers.smtp_hosts import resolve_smtp_host
from app.services.ssrf import SsrfError, validate_mail_host


def test_block_loopback_imap():
    with pytest.raises(ValueError):
        resolve_imap_host("a@b.com", imap_host="127.0.0.1", imap_port=993)


def test_block_private_smtp():
    with pytest.raises(ValueError):
        resolve_smtp_host("a@b.com", smtp_host="10.0.0.5", smtp_port=587)


def test_block_metadata_host():
    with pytest.raises(SsrfError):
        validate_mail_host("metadata.google.internal", port=993, resolve_dns=False)
    with pytest.raises(SsrfError):
        validate_mail_host("127.0.0.1", port=993, resolve_dns=False)


def test_block_weird_port():
    with pytest.raises(SsrfError):
        validate_mail_host("example.com", port=22, resolve_dns=False)


def test_gmail_table_ok():
    h = resolve_imap_host("user@gmail.com")
    assert h.host == "imap.gmail.com"
    assert h.port == 993


def test_smtp_gmail_ok():
    h = resolve_smtp_host("user@gmail.com")
    assert h.host == "smtp.gmail.com"
