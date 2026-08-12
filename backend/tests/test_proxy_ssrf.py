"""Client-supplied proxy URLs are an SSRF surface of their own."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.schemas import ProxyFetchRequest, SendMailRequest
from app.services.ssrf import SsrfError, validate_proxy_url


@pytest.mark.parametrize(
    "proxy",
    [
        "http://127.0.0.1:8080",
        "http://169.254.169.254:80",
        "socks5://10.0.0.5:1080",
        "http://[::1]:8080",
        "ftp://example.com:8080",
        "not-a-url",
    ],
)
def test_validate_proxy_url_blocks_private_and_bad_schemes(proxy: str) -> None:
    with pytest.raises(SsrfError):
        validate_proxy_url(proxy, allow_private=False)


def test_validate_proxy_url_allows_private_when_configured() -> None:
    assert (
        validate_proxy_url("socks5://warp-1:1080", allow_private=True)
        == "socks5://warp-1:1080"
    )


def test_validate_proxy_url_allows_public_host_with_mocked_dns() -> None:
    with patch("app.services.ssrf.pick_safe_ip", return_value="93.184.216.34"):
        out = validate_proxy_url("http://proxy.example.com:8080", allow_private=False)
    assert out == "http://proxy.example.com:8080"


def test_proxy_fetch_request_rejects_loopback_proxy() -> None:
    with pytest.raises(ValidationError):
        ProxyFetchRequest(
            email="a@b.com",
            provider="imap",
            password="x",
            proxy="http://127.0.0.1:9050",
        )


def test_send_mail_request_rejects_loopback_proxy() -> None:
    with pytest.raises(ValidationError):
        SendMailRequest(
            email="a@b.com",
            provider="imap",
            password="x",
            to=["b@c.com"],
            subject="hi",
            body_text="x",
            proxy="http://10.0.0.1:8080",
        )
