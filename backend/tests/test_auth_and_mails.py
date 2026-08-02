"""Health + legacy auth stubs (user/admin removed in local-first)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["master_key_configured"] is True


def test_api_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/register",
        "/api/auth/login",
        "/api/admin/login",
        "/api/me/mails/search",
    ],
)
def test_removed_auth_routes(client: TestClient, path: str) -> None:
    """User/admin routes are gone or return 404/410."""
    if "register" in path or "login" in path:
        r = client.post(path, json={})
    else:
        r = client.get(path)
    assert r.status_code in (404, 405, 410, 422)
