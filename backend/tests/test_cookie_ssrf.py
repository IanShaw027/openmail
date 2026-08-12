"""Cookie provider must not be an open outbound HTTP client.

Two attack shapes were confirmed against the pre-fix code:

1. ``session_meta.folder_url`` pointing at a metadata IP is fetched for real,
   and the response body is parsed back as a mail subject — readable SSRF.
2. ``site`` pointing at an attacker host makes Path B POST the user's plaintext
   password there. Generic DNS SSRF checks cannot stop (2): the host is public
   on purpose. An allowlist of United Internet properties stops both.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.providers.cookie_mailcom import (
    MailcomCookieProvider,
    _AllowlistedClient,
    _folder_urls,
    _sanitize_meta_urls,
    _sanitize_site,
    assert_mailcom_url,
)


def test_assert_mailcom_url_allows_united_internet_hosts() -> None:
    for url in (
        "https://www.mail.com/",
        "https://lightmailer.mail.com/folderlist",
        "https://login.gmx.net/login",
        "https://navigator-lxa.mail.com/login",
    ):
        assert assert_mailcom_url(url, resolve_dns=False) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.mail.com/",  # http, not https
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/",
        "https://evil.example/phish",
        "https://mail.com.evil.example/",  # suffix must be a real label boundary
        "https://notmail.com/",
        "file:///etc/passwd",
    ],
)
def test_assert_mailcom_url_rejects_non_properties(url: str) -> None:
    with pytest.raises(ValueError):
        assert_mailcom_url(url, resolve_dns=False)


def test_sanitize_site_rejects_attacker_host() -> None:
    with pytest.raises(ValueError, match="mail.com|Unsupported|https"):
        _sanitize_site("https://evil.example")
    with pytest.raises(ValueError, match="Unsupported"):
        _sanitize_site("evil.example")
    assert _sanitize_site("mail.com") == "mail.com"
    assert _sanitize_site("gmx.de") == "gmx.de"


def test_sanitize_meta_drops_poisoned_folder_url() -> None:
    cleaned = _sanitize_meta_urls(
        {
            "folder_url": "https://169.254.169.254/latest/meta-data/",
            "mailbox_url": "https://lightmailer.mail.com/folderlist",
            "start_url": "https://evil.example/x",
            "other": "kept",
        }
    )
    assert "folder_url" not in cleaned
    assert "start_url" not in cleaned
    assert cleaned["mailbox_url"] == "https://lightmailer.mail.com/folderlist"
    assert cleaned["other"] == "kept"


def test_folder_urls_never_includes_poisoned_meta() -> None:
    urls = _folder_urls(
        "mail.com",
        meta={"folder_url": "https://127.0.0.1/admin"},
    )
    assert all("127.0.0.1" not in u for u in urls)
    assert any("lightmailer.mail.com" in u for u in urls)


def test_allowlisted_client_blocks_redirect_off_property() -> None:
    inner = MagicMock()
    # First response: 302 to metadata
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"location": "https://169.254.169.254/latest/meta-data/"}
    inner.get.return_value = redirect

    client = _AllowlistedClient(inner)
    with pytest.raises(ValueError, match="mail.com|非"):
        client.get("https://www.mail.com/")
    # The inner client was asked for the allowlisted URL only — never the metadata IP.
    called_urls = [c.args[0] for c in inner.get.call_args_list]
    assert called_urls == ["https://www.mail.com/"]


def test_fetch_refuses_evil_site_before_any_http() -> None:
    provider = MailcomCookieProvider()
    account = SimpleNamespace(email="a@mail.com", proxy=None, password=None)
    with patch("app.providers.cookie_mailcom._http_client") as http_client:
        result = provider.fetch(
            account,
            credentials={"site": "https://evil.example", "password": "secret"},
        )
    assert result.ok is False
    assert "site" in (result.error or "").lower() or "http" in (result.error or "").lower() \
        or "支持" in (result.error or "") or "mail.com" in (result.error or "")
    http_client.assert_not_called()


def test_fetch_strips_poisoned_meta_and_still_attempts_restore() -> None:
    """A poisoned folder_url must not be fetched; restore may still use defaults."""
    provider = MailcomCookieProvider()
    account = SimpleNamespace(email="a@mail.com", proxy=None, password=None)

    fake_client = MagicMock()
    with patch("app.providers.cookie_mailcom._http_client", return_value=fake_client), patch.object(
        provider, "try_restore", return_value=(False, None)
    ) as restore, patch.object(
        provider, "full_login", return_value=(False, "no", None)
    ):
        provider.fetch(
            account,
            credentials={
                "site": "mail.com",
                "password": "secret",
                "cookies": [{"name": "sid", "value": "1"}],
                "session_meta": {
                    "folder_url": "https://169.254.169.254/latest/meta-data/",
                },
            },
        )

    assert restore.called
    meta = restore.call_args.kwargs.get("meta") or {}
    assert "folder_url" not in meta
