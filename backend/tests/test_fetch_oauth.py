"""OAuth Graph unit coverage via MockTransport (companion to test_fetch_http_api)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from app.models import ProviderType
from app.providers.oauth_graph import OAuthGraphProvider


def test_oauth_missing_credentials() -> None:
    provider = OAuthGraphProvider()
    account = SimpleNamespace(provider=ProviderType.oauth, email="a@b.com")
    result = provider.fetch(account, credentials={})
    assert result.ok is False
    assert "client_id" in (result.error or "")


def test_oauth_junk_folder_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(
                200,
                json={"access_token": "at", "token_type": "Bearer", "expires_in": 3600},
            )
        if "junkemail" in url:
            return httpx.Response(200, json={"value": []})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="a@b.com")
    result = provider.fetch(
        account,
        folder="junk",
        credentials={"client_id": "c", "refresh_token": "r"},
    )
    client.close()
    assert result.ok is True
    # Normalized to UI folder id (spam tab)
    assert result.folder == "spam"
    assert result.messages == []
