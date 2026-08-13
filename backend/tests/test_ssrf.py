"""SSRF protection tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ssrf import SsrfError, is_safe_url, validate_redirect_target, validate_url


def test_block_loopback_literal() -> None:
    with pytest.raises(SsrfError):
        validate_url("http://127.0.0.1/secret")
    with pytest.raises(SsrfError):
        validate_url("http://127.0.0.1:8080/")
    assert is_safe_url("http://127.0.0.1") is False


def test_block_metadata_link_local() -> None:
    with pytest.raises(SsrfError):
        validate_url("http://169.254.169.254/latest/meta-data/")
    assert is_safe_url("http://169.254.169.254") is False


def test_block_aliyun_metadata_and_cgnat() -> None:
    for url in (
        "http://100.100.100.200/",
        "http://100.100.100.200/latest/meta-data/",
        "http://100.64.0.1/",
        "http://100.127.255.254/",
    ):
        with pytest.raises(SsrfError):
            validate_url(url)
        assert is_safe_url(url) is False


def test_block_private_ranges() -> None:
    for url in (
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.5/",
        "http://[::1]/",
    ):
        with pytest.raises(SsrfError):
            validate_url(url)


def test_block_non_http_schemes() -> None:
    with pytest.raises(SsrfError):
        validate_url("file:///etc/passwd")
    with pytest.raises(SsrfError):
        validate_url("ftp://example.com/a")
    with pytest.raises(SsrfError):
        validate_url("gopher://example.com/")


def test_block_userinfo() -> None:
    with pytest.raises(SsrfError):
        validate_url("https://user:pass@example.com/")


def test_allow_example_com_with_mocked_dns() -> None:
    # Mock DNS so we don't depend on network; resolve to public IP
    with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
        out = validate_url("https://example.com/path")
        assert out == "https://example.com/path"
        assert is_safe_url("https://example.com/") is True


def test_block_hostname_resolving_to_private() -> None:
    with patch("app.services.ssrf._resolve_host", return_value=["10.1.2.3"]):
        with pytest.raises(SsrfError):
            validate_url("https://evil.internal.example/")


def test_redirect_target_blocked() -> None:
    with pytest.raises(SsrfError):
        validate_redirect_target("https://example.com/", "http://127.0.0.1/admin")


def test_redirect_relative_allowed_with_public_dns() -> None:
    with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
        out = validate_redirect_target("https://example.com/a", "/b")
        assert out.startswith("https://example.com/")
