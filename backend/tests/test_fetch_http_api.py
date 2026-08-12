"""HttpApi provider + oauth graph mocked with httpx MockTransport."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.models import ProviderType
from app.providers.base import reset_registry_for_tests
from app.providers.http_api import HttpApiProvider, extract_message_list, normalize_message_item
from app.providers.oauth_graph import OAuthGraphProvider, TOKEN_URL
from app.services.fetch_service import fetch_proxy


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_expand_api_url_candidates_bare_worker() -> None:
    from app.providers.http_api import expand_api_url_candidates

    c = expand_api_url_candidates("https://example-mail-admin.workers.dev")
    assert c[0].startswith("https://example-mail-admin.workers.dev")
    assert any(x.endswith("/api/mails") for x in c)
    # Full path should not expand
    single = expand_api_url_candidates("https://x.workers.dev/api/mails")
    assert single == ["https://x.workers.dev/api/mails"]


def test_normalize_generic_message() -> None:
    msg = normalize_message_item(
        {
            "id": "m1",
            "subject": "Your code is 998877",
            "from": "noreply@x.com",
            "body_text": "code 998877",
        }
    )
    assert msg.id == "m1"
    assert msg.verification_code == "998877"


def test_normalize_ian10_mail_admin_list_row() -> None:
    """CF worker list shape (ian10-mail-admin mailListRow)."""
    msg = normalize_message_item(
        {
            "id": 310362,
            "domain": "qazwc.com",
            "created_at": "2026-08-04 09:12:17",
            "recipient": "46htbot22s6o@kv8wl0tyjx.qazwc.com",
            "from": "OpenAI <trustandsafety@tm.openai.com>",
            "subject": "OpenAI - Access Deactivated",
            "code": "ABC123",
            "message_id": "mid-1",
            "text": "Hello, your account…",
        }
    )
    assert msg.id == "310362"
    assert msg.to == "46htbot22s6o@kv8wl0tyjx.qazwc.com"
    assert msg.date is not None
    assert "2026-08-04" in msg.date
    assert msg.from_address == "trustandsafety@tm.openai.com" or "openai" in (msg.from_ or "").lower()
    assert msg.body_text.startswith("Hello")
    assert msg.verification_code == "ABC123"


def test_normalize_created_at_epoch_ms() -> None:
    msg = normalize_message_item(
        {
            "id": "e1",
            "subject": "hi",
            "recipient": "a@b.com",
            "created_at": 1_722_768_000_000,
            "text": "x",
        }
    )
    assert msg.to == "a@b.com"
    assert msg.date is not None


def test_extract_message_list_shapes() -> None:
    assert len(extract_message_list({"messages": [{"id": "1", "subject": "x"}]})) == 1
    assert len(extract_message_list({"data": [{"id": "2", "subject": "y"}]})) == 1
    assert len(extract_message_list({"data": {"messages": [{"id": "3", "subject": "z"}]}})) == 1
    assert len(extract_message_list([{"id": "4", "subject": "w"}])) == 1


def test_build_api_auth_headers_none_and_auto() -> None:
    from app.providers.http_api import build_api_auth_headers

    assert build_api_auth_headers({}) == {}
    assert build_api_auth_headers({"api_auth_style": "none", "api_key": "x"}) == {}
    auto = build_api_auth_headers({"api_key": "s3cret", "api_auth_style": "auto"})
    assert auto["x-admin-auth"] == "s3cret"
    assert auto["X-API-Key"] == "s3cret"
    assert auto["Authorization"] == "Bearer s3cret"
    admin = build_api_auth_headers({"password": "adm", "api_auth_style": "x-admin-auth"})
    assert admin == {"x-admin-auth": "adm"}


def test_extract_mailbox_list_moemail_shape() -> None:
    from app.providers.http_api import extract_mailbox_list

    payload = {
        "emails": [
            {"id": "1", "address": "a@temp.dev"},
            {"id": "2", "address": "b@temp.dev"},
        ],
        "total": 2,
    }
    assert extract_mailbox_list(payload) == ["a@temp.dev", "b@temp.dev"]


def test_http_api_filters_messages_by_mailbox_email() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": "1",
                        "subject": "for A",
                        "to": "alice@temp.dev",
                        "body_text": "code 111111",
                    },
                    {
                        "id": "2",
                        "subject": "for B",
                        "to": "bob@temp.dev",
                        "body_text": "code 222222",
                    },
                ],
                "emails": [
                    {"address": "alice@temp.dev"},
                    {"address": "bob@temp.dev"},
                ],
            },
        )

    client = httpx.Client(transport=_mock_transport(handler))
    provider = HttpApiProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.http_api, email="alice@temp.dev")
    with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
        result = provider.fetch(
            account,
            credentials={"api_url": "https://example.com/mail.json"},
        )
    client.close()
    assert result.ok is True
    assert result.message_count == 1
    assert result.messages[0].subject == "for A"
    assert result.credential_updates is not None
    assert result.credential_updates.mailboxes == ["alice@temp.dev", "bob@temp.dev"]


def test_http_api_fetch_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Pin-to-IP closes the DNS-rebinding window: the TCP peer is the
        # address we just validated, while Host + sni_hostname keep the
        # original name so Cloudflare / workers.dev TLS still works.
        assert "93.184.216.34" in str(request.url)
        assert request.headers.get("host") == "example.com"
        assert request.extensions.get("sni_hostname") == "example.com"
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": "1",
                        "subject": "OTP 123456",
                        "from": "a@b.com",
                        "body_text": "Your OTP is 123456",
                    }
                ]
            },
        )

    client = httpx.Client(transport=_mock_transport(handler))
    provider = HttpApiProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.http_api, email="u@example.com")

    with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
        result = provider.fetch(
            account,
            credentials={"api_url": "https://example.com/mail.json"},
        )
    client.close()

    assert result.ok is True
    assert result.message_count == 1
    assert result.messages[0].verification_code == "123456"


def test_http_api_ssrf_blocked_loopback() -> None:
    provider = HttpApiProvider()
    account = SimpleNamespace(provider=ProviderType.http_api, email="u@example.com")
    result = provider.fetch(
        account,
        credentials={"api_url": "http://127.0.0.1/secret"},
    )
    assert result.ok is False
    assert result.error is not None
    assert "SSRF" in result.error or "拦截" in result.error


def test_http_api_ssrf_blocked_metadata() -> None:
    provider = HttpApiProvider()
    account = SimpleNamespace(provider=ProviderType.http_api, email="u@example.com")
    result = provider.fetch(
        account,
        credentials={"api_url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert result.ok is False
    assert result.error is not None


def test_http_api_redirect_to_private_blocked() -> None:
    hops = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hops["n"] += 1
        if hops["n"] == 1:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/x"})
        return httpx.Response(200, json={"messages": []})

    client = httpx.Client(transport=_mock_transport(handler), follow_redirects=False)
    provider = HttpApiProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.http_api, email="u@example.com")

    with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
        result = provider.fetch(
            account,
            credentials={"api_url": "https://example.com/start"},
        )
    client.close()
    assert result.ok is False
    assert result.error is not None


def test_oauth_graph_refresh_and_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(
                200,
                json={
                    "access_token": "at_test",
                    "refresh_token": "rt_new",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if "graph.microsoft.com" in url and "/messages/" in url and not url.rstrip("/").endswith("messages"):
            # single message body
            return httpx.Response(
                200,
                json={
                    "id": "msg1",
                    "subject": "Your code 654321",
                    "from": {"emailAddress": {"name": "N", "address": "n@x.com"}},
                    "toRecipients": [],
                    "receivedDateTime": "2026-08-01T12:00:00Z",
                    "bodyPreview": "code 654321",
                    "body": {"contentType": "text", "content": "Your verification code is 654321"},
                },
            )
        if "graph.microsoft.com" in url and "messages" in url:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg1",
                            "subject": "Your code 654321",
                            "from": {"emailAddress": {"name": "N", "address": "n@x.com"}},
                            "toRecipients": [],
                            "receivedDateTime": "2026-08-01T12:00:00Z",
                            "bodyPreview": "code 654321",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=_mock_transport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="user@outlook.com")
    result = provider.fetch(
        account,
        credentials={"client_id": "cid", "refresh_token": "rt_old"},
        quick=True,
    )
    client.close()

    assert result.ok is True
    assert len(result.messages) == 1
    assert result.messages[0].verification_code == "654321"
    assert result.credential_updates is not None
    assert result.credential_updates.refresh_token == "rt_new"
    assert result.credential_updates.access_token == "at_test"


def test_oauth_invalid_grant_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "AADSTS70000: The refresh token has expired",
            },
        )

    client = httpx.Client(transport=_mock_transport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="user@outlook.com")
    result = provider.fetch(
        account,
        credentials={"client_id": "cid", "refresh_token": "bad"},
    )
    client.close()
    assert result.ok is False
    assert result.error is not None
    assert "刷新令牌" in result.error or "Refresh token" in result.error


def test_proxy_fetch_http_api_not_persisted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": "x",
                        "subject": "code 111222",
                        "body_text": "验证码 111222",
                        "from": "z@z.com",
                    }
                ]
            },
        )

    client = httpx.Client(transport=_mock_transport(handler))
    # Inject provider with mock client into registry
    from app.providers.http_api import HttpApiProvider as HAP
    from app.providers.oauth_graph import OAuthGraphProvider as OGP

    reset_registry_for_tests([OGP(), HAP(client=client)])
    try:
        with patch("app.services.ssrf._resolve_host", return_value=["93.184.216.34"]):
            result = fetch_proxy(
                email="guest@example.com",
                provider=ProviderType.http_api,
                credential={"api_url": "https://example.com/api"},
            )
    finally:
        reset_registry_for_tests(None)
        client.close()

    assert result.ok is True
    assert result.code == "111222"
    assert result.email == "guest@example.com"
