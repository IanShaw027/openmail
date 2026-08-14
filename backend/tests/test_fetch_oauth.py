"""OAuth Graph unit coverage via MockTransport (companion to test_fetch_http_api)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from unittest.mock import patch

from app.models import ProviderType
from app.providers.base import FetchResult
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
        if (
            "mailFolders" in url
            and "/messages/" in url
            and "$top=" not in url
            and "$select=" in url
        ):
            msg_id = url.split("/messages/")[1].split("?")[0]
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


def test_oauth_graph_invalid_grant_falls_back_to_imap() -> None:
    token_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com" in url:
            body = request.content.decode()
            token_bodies.append(body)
            if "graph.microsoft.com" in body:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "AADSTS70000: The provided grant has expired due to it being revoked",
                        "error_codes": [70000],
                    },
                )
            if "outlook.office.com" in body and "IMAP" in body:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "imap_at",
                        "refresh_token": "rt_rotated",
                        "expires_in": 3600,
                        "scope": "https://outlook.office.com/IMAP.AccessAsUser.All",
                    },
                )
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="user@hotmail.com")
    imap_result = FetchResult(ok=True, messages=[], folder="inbox")
    with patch(
        "app.providers.imap_provider.ImapProvider.fetch",
        return_value=imap_result,
    ) as imap_fetch:
        result = provider.fetch(
            account,
            credentials={"client_id": "cid-generic", "refresh_token": "rt_old"},
        )
    client.close()

    assert result.ok is True
    assert any("graph.microsoft.com" in body for body in token_bodies)
    assert any("IMAP.AccessAsUser.All" in body for body in token_bodies)
    assert result.credential_updates is not None
    assert result.credential_updates.refresh_token == "rt_rotated"
    assert (result.credential_updates.session_meta or {}).get("oauth_transport") == "imap"
    imap_fetch.assert_called_once()
    passed = imap_fetch.call_args.kwargs.get("credentials") or {}
    assert passed.get("access_token") == "imap_at"


def test_oauth_thunderbird_client_skips_graph_refresh() -> None:
    token_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            token_bodies.append(request.content.decode())
            return httpx.Response(
                200,
                json={"access_token": "imap_at", "refresh_token": "rt_new", "expires_in": 3600},
            )
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    account = SimpleNamespace(provider=ProviderType.oauth, email="user@hotmail.com")
    with patch(
        "app.providers.imap_provider.ImapProvider.fetch",
        return_value=FetchResult(ok=True, messages=[], folder="inbox"),
    ):
        result = provider.fetch(
            account,
            credentials={
                "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
                "refresh_token": "rt_old",
            },
        )
    client.close()

    assert result.ok is True
    assert token_bodies
    assert all("graph.microsoft.com" not in body for body in token_bodies)
    assert any("IMAP.AccessAsUser.All" in body for body in token_bodies)


def test_oauth_headers_phase_omits_body_select() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "mailFolders" in url and "/messages" in url:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "AAMkA==",
                            "subject": "code 123456 in preview",
                            "bodyPreview": "Your code is 123456",
                            "from": {"emailAddress": {"address": "n@x.com"}},
                            "receivedDateTime": "2026-08-01T12:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    result = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={"client_id": "c", "refresh_token": "r"},
        limits={"phase": "headers", "max_messages": 5},
    )
    client.close()
    assert result.ok is True
    assert result.phase == "headers"
    assert result.pending_body_ids == ["AAMkA=="]
    list_urls = [u for u in seen_urls if "mailFolders" in u]
    assert list_urls
    select = list_urls[0].split("$select=")[1].split("&")[0]
    assert "body" not in select.split(",")
    assert not any("/me/messages/" in u and "mailFolders" not in u for u in seen_urls)
    assert result.messages[0].verification_code == "123456"
    assert result.messages[0].body_preview
    assert not (result.messages[0].body_text or "").strip()
    assert not (result.messages[0].body_html or "").strip()


def test_oauth_reuses_supplied_access_token_without_refresh() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        if "mailFolders" in url:
            return httpx.Response(200, json={"value": []})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    result = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={
            "client_id": "c",
            "refresh_token": "r",
            "access_token": "cached-at",
            "token_expires_at": 9_999_999_999,
        },
        limits={"phase": "headers", "max_messages": 5},
    )
    client.close()
    assert result.ok is True
    assert not any("login.microsoftonline.com" in u for u in seen_urls)


def test_oauth_expired_access_token_refreshes_and_retries() -> None:
    seen_urls: list[str] = []
    auths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        auths.append(str(request.headers.get("authorization") or ""))
        if "mailFolders" in url:
            return httpx.Response(200, json={"value": []})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    result = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={
            "client_id": "c",
            "refresh_token": "r",
            "access_token": "stale",
            "token_expires_at": 1,
        },
        limits={"phase": "headers", "max_messages": 5},
    )
    client.close()
    assert result.ok is True
    assert any("login.microsoftonline.com" in u for u in seen_urls)
    assert any(a.endswith("fresh") for a in auths)


def test_oauth_stale_token_401_refreshes_once() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        auth = str(request.headers.get("authorization") or "")
        if "mailFolders" in url and auth.endswith("stale"):
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        if "mailFolders" in url:
            return httpx.Response(200, json={"value": []})
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    result = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={
            "client_id": "c",
            "refresh_token": "r",
            "access_token": "stale",
            "token_expires_at": 9_999_999_999,
        },
        limits={"phase": "headers", "max_messages": 5},
    )
    client.close()
    assert result.ok is True
    assert any("login.microsoftonline.com" in u for u in seen_urls)


def test_oauth_bodies_phase_skips_folder_mismatch() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "mailFolders/inbox/messages/" in url:
            return httpx.Response(404, json={})
        if "/me/messages/" in url and "mailFolders" not in url:
            return httpx.Response(
                200,
                json={
                    "id": "AAMkOther==",
                    "parentFolderId": "folder-spam",
                    "body": {"contentType": "text", "content": "OTP 999111"},
                    "bodyPreview": "OTP 999111",
                    "subject": "other folder",
                    "from": {"emailAddress": {"address": "n@x.com"}},
                    "receivedDateTime": "2026-08-01T12:00:00Z",
                },
            )
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    result = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={"client_id": "c", "refresh_token": "r"},
        folder="inbox",
        limits={"phase": "bodies", "body_ids": ["AAMkOther=="]},
    )
    client.close()
    assert result.ok is False
    assert result.messages == []
    assert not any("/me/messages/" in u and "mailFolders" not in u for u in seen_urls)


def test_oauth_bodies_phase_rejects_path_traversal_id() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "/messages/" in url and "mailFolders" in url and "$top" not in url:
            return httpx.Response(
                200,
                json={
                    "id": "AAMkA==",
                    "parentFolderId": "ok",
                    "body": {"contentType": "text", "content": "OTP 654321"},
                    "bodyPreview": "OTP 654321",
                    "subject": "hi",
                    "from": {"emailAddress": {"address": "n@x.com"}},
                    "receivedDateTime": "2026-08-01T12:00:00Z",
                },
            )
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OAuthGraphProvider(client=client)
    bad = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={"client_id": "c", "refresh_token": "r"},
        limits={"phase": "bodies", "body_ids": ["../users/other/messages/x"]},
    )
    good = provider.fetch(
        SimpleNamespace(provider=ProviderType.oauth, email="a@b.com"),
        credentials={"client_id": "c", "refresh_token": "r"},
        limits={"phase": "bodies", "body_ids": ["AAMkA=="]},
    )
    client.close()
    assert bad.ok is True
    assert bad.messages == []
    assert not any("../users" in u for u in seen_urls)
    assert good.ok is True
    assert good.messages and "654321" in (good.messages[0].body_text or "")
