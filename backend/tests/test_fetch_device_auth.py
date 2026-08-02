"""API-level device auth for fetch/send/accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import pytest
from fastapi.testclient import TestClient


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    return secret, b64, pid


def _sign(secret: bytes, method: str, path: str) -> dict[str, str]:
    ts = str(int(time.time()))
    msg = f"{ts}.{method.upper()}.{path}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return {
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
    }


@pytest.fixture
def vault_device(client: TestClient, monkeypatch, tmp_path):
    """Register a vault device and return (headers_factory, public_id)."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    import app.config as cfg

    cfg.get_settings.cache_clear()
    import app.services.device_auth as da
    from importlib import reload

    da = reload(da)
    da._loaded = False
    da._secrets.clear()
    da._registry.clear()

    secret, b64, pid = _pair()
    out = da.register_device_secret(pid, b64)

    def headers(method: str, path: str) -> dict[str, str]:
        h = {"X-Device-Id": out}
        h.update(_sign(secret, method, path))
        return h

    yield headers, out
    cfg.get_settings.cache_clear()


def test_accounts_list_rejects_legacy(client: TestClient):
    r = client.get("/api/accounts", headers={"X-Device-Id": "dev_forged_device_xx"})
    assert r.status_code == 401
    assert "vault" in r.json().get("detail", "").lower() or "vk" in r.json().get(
        "detail", ""
    ).lower()


def test_accounts_list_ok_with_hmac(client: TestClient, vault_device):
    headers_fn, _pid = vault_device
    r = client.get("/api/accounts", headers=headers_fn("GET", "/api/accounts"))
    assert r.status_code == 200
    assert r.json() == []


def test_fetch_stored_requires_hmac(client: TestClient):
    r = client.post(
        "/api/accounts/acc_x/fetch",
        headers={"X-Device-Id": "dev_forged_device_xx"},
    )
    assert r.status_code == 401


def test_send_requires_device_id(client: TestClient):
    r = client.post(
        "/api/fetch/send",
        json={"email": "a@b.com", "to": ["c@d.com"], "subject": "x", "body_text": "y"},
    )
    # Now requires vault HMAC — 400 missing id or 401 vault required
    assert r.status_code in (400, 401)


def test_proxy_fetch_rejects_random_device(client: TestClient):
    r = client.post(
        "/api/fetch/proxy",
        headers={"X-Device-Id": "dev_random_open_proxy_01"},
        json={
            "email": "a@b.com",
            "provider": "http_api",
            "credential": {"api_url": "https://example.com/"},
        },
    )
    assert r.status_code == 401


def test_send_with_device_id_reaches_handler(client: TestClient, vault_device):
    """With device + hmac, send runs (may fail provider) but not auth error."""
    headers_fn, _ = vault_device
    h = headers_fn("POST", "/api/fetch/send")
    r = client.post(
        "/api/fetch/send",
        headers=h,
        json={
            "email": "user@gmail.com",
            "provider": "imap",
            "password": "not-a-real-app-password",
            "to": ["other@example.com"],
            "subject": "t",
            "body_text": "b",
        },
    )
    # Not 401/400 device — provider/SMTP failure is fine
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


def test_public_config_hides_cloud_used_without_hmac(client: TestClient):
    r = client.get(
        "/api/config/public",
        headers={"X-Device-Id": "vk_" + "b" * 40},
    )
    assert r.status_code == 200
    body = r.json()
    # unauthenticated probe must not leak cloud_used for arbitrary ids
    assert "cloud_used" not in (body.get("quota") or {})
