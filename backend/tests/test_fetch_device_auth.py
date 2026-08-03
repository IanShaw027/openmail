"""API-level device auth for fetch/send/accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    return secret, b64, pid


def _sign(secret: bytes, method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
    """Sign with body-hash binding: {ts}.{METHOD}.{path}.{body_sha256}."""
    if body is None:
        body_bytes = b""
    elif isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    ts = str(int(time.time()))
    msg = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return {
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
        "X-Device-Body-Sha256": body_hash,
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

    def headers(method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
        h = {"X-Device-Id": out}
        h.update(_sign(secret, method, path, body))
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
    """With device + hmac + body hash, send runs (may fail provider) but not auth error."""
    headers_fn, _ = vault_device
    payload = {
        "email": "user@gmail.com",
        "provider": "imap",
        "password": "not-a-real-app-password",
        "to": ["other@example.com"],
        "subject": "t",
        "body_text": "b",
    }
    # Sign exact wire bytes and send the same bytes (avoid json= re-serialization drift)
    body_bytes = json.dumps(payload).encode("utf-8")
    h = headers_fn("POST", "/api/fetch/send", body_bytes)
    h["Content-Type"] = "application/json"
    r = client.post(
        "/api/fetch/send",
        headers=h,
        content=body_bytes,
    )
    # Not 401/400 device — provider/SMTP failure is fine
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


def test_post_without_body_hash_rejected(client: TestClient, vault_device):
    """POST with legacy path-only signature (no body hash header) is rejected."""
    headers_fn, pid = vault_device
    # Build legacy signature manually
    import app.services.device_auth as da

    # Use the registered secret from headers_fn by re-signing path-only
    secret = None
    for s in da._secrets.values():
        secret = s
        break
    assert secret is not None
    ts = str(int(time.time()))
    msg = f"{ts}.POST./api/fetch/send".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    h = {
        "X-Device-Id": pid,
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
    }
    r = client.post(
        "/api/fetch/send",
        headers=h,
        json={
            "email": "user@gmail.com",
            "provider": "imap",
            "password": "x",
            "to": ["other@example.com"],
            "subject": "t",
            "body_text": "b",
        },
    )
    assert r.status_code == 401


def test_body_hash_mismatch_rejected(client: TestClient, vault_device):
    headers_fn, _ = vault_device
    payload = {
        "email": "user@gmail.com",
        "provider": "imap",
        "password": "x",
        "to": ["other@example.com"],
        "subject": "t",
        "body_text": "b",
    }
    signed = json.dumps(payload).encode("utf-8")
    h = headers_fn("POST", "/api/fetch/send", signed)
    h["Content-Type"] = "application/json"
    # Tamper body after signing
    tampered = json.dumps({**payload, "body_text": "TAMPERED"}).encode("utf-8")
    r = client.post(
        "/api/fetch/send",
        headers=h,
        content=tampered,
    )
    assert r.status_code == 401


def test_public_config_hides_cloud_used_without_hmac(client: TestClient):
    r = client.get(
        "/api/config/public",
        headers={"X-Device-Id": "vk_" + "b" * 40},
    )
    assert r.status_code == 200
    body = r.json()
    # unauthenticated probe must not leak cloud_used for arbitrary ids
    assert "cloud_used" not in (body.get("quota") or {})


def test_account_quota_requires_vault_hmac(client: TestClient):
    r = client.get(
        "/api/accounts/meta/quota",
        headers={"X-Device-Id": "vk_" + "c" * 40},
    )
    assert r.status_code == 401


def test_stored_fetch_forwards_pagination_args(monkeypatch):
    from types import SimpleNamespace

    from app.models import Account, AccountPool, AccountStatus, ProviderType
    from app.routers import fetch as fetch_router

    calls: dict[str, object] = {}

    def fake_fetch_account(db, account, **kwargs):
        calls["db"] = db
        calls["account"] = account
        calls["kwargs"] = kwargs
        return SimpleNamespace(
            ok=True,
            messages=[],
            message_count=0,
            folder=kwargs.get("folder", "inbox"),
            fetched_at=None,
            code=None,
            cached=False,
            error=None,
            email=account.email,
            account_id=account.id,
            subject=None,
            from_=None,
            date=None,
            retry_after=None,
            session_cookies=None,
            session_meta=None,
            session_restored=False,
            mailboxes=None,
            uidvalidity=7,
        )

    monkeypatch.setattr(fetch_router, "fetch_account", fake_fetch_account)
    monkeypatch.setattr(
        fetch_router,
        "check_poll_quota",
        lambda *a, **k: (True, None),
    )
    settings = SimpleNamespace(quota_max_poll_per_hour=1000)
    db = SimpleNamespace(
        get=lambda _model, _id: Account(
            id="acc_fetch",
            email="user@example.com",
            owner_user_id="vk_test_device",
            provider=ProviderType.imap,
            pool=AccountPool.user_private,
            status=AccountStatus.ok,
        ),
        commit=lambda: None,
    )
    out = fetch_router.fetch_stored_account(
        account_id="acc_fetch",
        db=db,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        device_id="vk_test_device",
        folder="spam",
        quick=False,
        since="2026-08-01T00:00:00Z",
        before="2026-08-02T00:00:00Z",
        max_messages=25,
        full=True,
    )
    assert out.folder == "spam"
    assert calls["kwargs"] == {
        "folder": "spam",
        "quick": False,
        "settings": settings,
        "since": "2026-08-01T00:00:00Z",
        "before": "2026-08-02T00:00:00Z",
        "max_messages": 25,
        "full": True,
    }


def test_cloud_account_quota_reserves_and_releases(client: TestClient, vault_device, monkeypatch):
    import app.config as cfg

    headers_fn, _ = vault_device
    monkeypatch.setenv("QUOTA_MAX_CLOUD_ACCOUNTS", "1")
    cfg.get_settings.cache_clear()

    body1 = {
        "email": "first@example.com",
        "provider": "imap",
        "client_sealed": "sealed-blob-1-12345",
    }
    content1 = json.dumps(body1).encode("utf-8")
    r1 = client.post(
        "/api/accounts",
        headers={**headers_fn("POST", "/api/accounts", content1), "Content-Type": "application/json"},
        content=content1,
    )
    assert r1.status_code == 201
    acc1 = r1.json()["id"]

    body2 = {
        "email": "second@example.com",
        "provider": "imap",
        "client_sealed": "sealed-blob-2-12345",
    }
    content2 = json.dumps(body2).encode("utf-8")
    r2 = client.post(
        "/api/accounts",
        headers={**headers_fn("POST", "/api/accounts", content2), "Content-Type": "application/json"},
        content=content2,
    )
    assert r2.status_code == 429

    r3 = client.delete(
        f"/api/accounts/{acc1}",
        headers=headers_fn("DELETE", f"/api/accounts/{acc1}"),
    )
    assert r3.status_code == 204

    r4 = client.post(
        "/api/accounts",
        headers={**headers_fn("POST", "/api/accounts", content2), "Content-Type": "application/json"},
        content=content2,
    )
    assert r4.status_code == 201
