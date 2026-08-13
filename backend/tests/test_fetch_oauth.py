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


def test_oauth_expands_body_for_every_listed_message() -> None:
    """A page of 30 must not stop expanding HTML after 25 (OTP often lives in body)."""
    detail_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "/me/messages/" in url and "$select=" in url and "body" in url:
            msg_id = url.split("/me/messages/")[1].split("?")[0]
            detail_ids.append(msg_id)
            return httpx.Response(
                200,
                json={
                    "id": msg_id,
                    "subject": f"code for {msg_id}",
                    "bodyPreview": "preview",
                    "body": {"contentType": "text", "content": f"OTP 111{msg_id[-3:]}"},
                    "from": {"emailAddress": {"address": "n@x.com"}},
                    "receivedDateTime": "2026-08-01T12:00:00Z",
                },
            )
        if "mailFolders" in url and "/messages" in url:
            items = [
                {
                    "id": f"msg{i:02d}",
                    "subject": f"row {i}",
                    "from": {"emailAddress": {"address": "n@x.com"}},
                    "receivedDateTime": "2026-08-01T12:00:00Z",
                    "bodyPreview": "preview only",
                }
                for i in range(30)
            ]
            return httpx.Response(200, json={"value": items})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="a@b.com")
    result = provider.fetch(
        account,
        credentials={"client_id": "c", "refresh_token": "r"},
        limits={"max_messages": 30},
        quick=False,
    )
    client.close()
    assert result.ok is True
    assert len(result.messages) == 30
    assert len(detail_ids) == 30
    assert all(m.body_text for m in result.messages)
