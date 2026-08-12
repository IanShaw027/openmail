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


def test_smtp_gmx_zoho_netease_table():
    from unittest.mock import patch

    # Table lookup is what this test covers; DNS is unrelated and flakes offline.
    with patch("app.services.ssrf.pick_safe_ip", return_value="93.184.216.34"):
        assert resolve_smtp_host("a@gmx.com").host == "mail.gmx.com"
        assert resolve_smtp_host("a@gmx.de").host == "mail.gmx.net"
        assert resolve_smtp_host("a@zoho.com").host == "smtp.zoho.com"
        assert resolve_smtp_host("a@zohomail.com").host == "smtp.zoho.com"
        assert resolve_smtp_host("a@126.com").host == "smtp.126.com"
        # imap host → smtp swap for GMX/Zoho
        assert resolve_smtp_host("a@x.com", smtp_host="imap.gmx.com").host == "mail.gmx.com"
        assert resolve_smtp_host("a@x.com", smtp_host="imap.zoho.com").host == "smtp.zoho.com"


def test_imap_gmx_zoho_table():
    from unittest.mock import patch

    with patch("app.services.ssrf.pick_safe_ip", return_value="93.184.216.34"):
        assert resolve_imap_host("a@gmx.com").host == "imap.gmx.com"
        assert resolve_imap_host("a@zoho.com").host == "imap.zoho.com"
