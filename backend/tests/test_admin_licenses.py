"""Admin-issued license codes (OPENMAIL_ADMIN_DEVICE_IDS)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import LicenseCode, LicenseCodeUse


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    return secret, b64, pid


def _sign(secret: bytes, method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
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


def _register(client: TestClient):
    secret, b64, pid = _pair()
    r = client.post("/api/device/register", json={"public_id": pid, "secret_b64": b64})
    assert r.status_code == 200, r.text
    out = r.json()["public_id"]
    status = r.json()["status"]

    def headers(method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
        h = {"X-Device-Id": out}
        h.update(_sign(secret, method, path, body))
        return h

    return headers, out, status


def _as_admin(monkeypatch, pid: str) -> None:
    monkeypatch.setenv("OPENMAIL_ADMIN_DEVICE_IDS", pid)
    get_settings.cache_clear()


def test_admin_licenses_forbidden_when_allowlist_empty(client: TestClient):
    headers, _pid, status = _register(client)
    assert status == "trusted"
    r = client.get("/api/admin/licenses", headers=headers("GET", "/api/admin/licenses"))
    assert r.status_code == 403


def test_admin_licenses_forbidden_for_non_admin_trusted(client: TestClient, monkeypatch):
    headers, pid, _ = _register(client)
    _as_admin(monkeypatch, "vk_someone_else_not_this_device_xx")
    r = client.get("/api/admin/licenses", headers=headers("GET", "/api/admin/licenses"))
    assert r.status_code == 403


def test_pending_device_cannot_be_admin_even_if_listed(client: TestClient, monkeypatch):
    _admin_h, _admin_pid, _ = _register(client)
    pending_h, pending_pid, pending_status = _register(client)
    assert pending_status == "pending"
    _as_admin(monkeypatch, pending_pid)
    r = client.get("/api/admin/licenses", headers=pending_h("GET", "/api/admin/licenses"))
    assert r.status_code == 401


def test_device_me_is_admin_flag(client: TestClient, monkeypatch):
    headers, pid, _ = _register(client)
    r = client.get("/api/device/me", headers=headers("GET", "/api/device/me"))
    assert r.status_code == 200
    assert r.json()["is_admin"] is False

    _as_admin(monkeypatch, f"{pid}\n vk_other")
    r = client.get("/api/device/me", headers=headers("GET", "/api/device/me"))
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_admin_issue_list_revoke_and_usage(client: TestClient, monkeypatch):
    admin_h, admin_pid, _ = _register(client)
    user_h, user_pid, user_status = _register(client)
    assert user_status == "pending"

    # Approve the second device so it can present HMAC on /config/public
    approve_body = json.dumps({"public_id": user_pid}).encode()
    h = admin_h("POST", "/api/device/approve", approve_body)
    h["Content-Type"] = "application/json"
    r = client.post(
        "/api/device/approve",
        headers=h,
        content=approve_body,
    )
    assert r.status_code == 200, r.text

    _as_admin(monkeypatch, admin_pid)

    create_body = json.dumps({"note": "friends"}).encode()
    h = admin_h("POST", "/api/admin/licenses", create_body)
    h["Content-Type"] = "application/json"
    r = client.post("/api/admin/licenses", headers=h, content=create_body)
    assert r.status_code == 200, r.text
    created = r.json()
    token = created["token"]
    assert token.startswith("om_")
    assert created["note"] == "friends"
    assert created["revoked_at"] is None
    assert created["created_by"] == admin_pid
    assert created["device_count"] == 0
    license_id = created["id"]

    r = client.get("/api/admin/licenses", headers=admin_h("GET", "/api/admin/licenses"))
    assert r.status_code == 200
    rows = r.json()["licenses"]
    assert len(rows) == 1
    assert rows[0]["token"] == token
    assert rows[0]["devices"] == []

    # Issued token unlocks quota for a proven HMAC device
    cfg_headers = user_h("GET", "/api/config/public")
    cfg_headers["X-License-Token"] = token
    r = client.get("/api/config/public", headers=cfg_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["licensed"] is True
    assert body["quota"]["licensed"] is True

    r = client.get("/api/admin/licenses", headers=admin_h("GET", "/api/admin/licenses"))
    listed = r.json()["licenses"][0]
    assert listed["device_count"] == 1
    assert listed["last_used_at"] is not None
    assert listed["devices"][0]["device_id"] == user_pid

    # Unauthenticated probe with the token must not mint a fake device row
    r = client.get("/api/config/public", headers={"X-License-Token": token, "X-Device-Id": "vk_forged"})
    assert r.status_code == 200
    r = client.get("/api/admin/licenses", headers=admin_h("GET", "/api/admin/licenses"))
    listed = r.json()["licenses"][0]
    ids = {d["device_id"] for d in listed["devices"]}
    assert ids == {user_pid}

    revoke_path = f"/api/admin/licenses/{license_id}/revoke"
    r = client.post(revoke_path, headers=admin_h("POST", revoke_path, b""))
    assert r.status_code == 200, r.text
    assert r.json()["revoked_at"] is not None

    cfg_headers = user_h("GET", "/api/config/public")
    cfg_headers["X-License-Token"] = token
    r = client.get("/api/config/public", headers=cfg_headers)
    assert r.status_code == 200
    assert r.json()["licensed"] is False


def test_env_license_tokens_still_work(client: TestClient, monkeypatch):
    headers, _pid, _ = _register(client)
    monkeypatch.setenv("LICENSE_TOKENS", "env-secret-token")
    get_settings.cache_clear()
    cfg_headers = headers("GET", "/api/config/public")
    cfg_headers["X-License-Token"] = "env-secret-token"
    r = client.get("/api/config/public", headers=cfg_headers)
    assert r.status_code == 200
    assert r.json()["licensed"] is True


def test_issued_token_ciphertext_not_plaintext(client: TestClient, monkeypatch, db_session):
    admin_h, admin_pid, _ = _register(client)
    _as_admin(monkeypatch, admin_pid)
    empty = b"{}"
    h = admin_h("POST", "/api/admin/licenses", empty)
    h["Content-Type"] = "application/json"
    r = client.post(
        "/api/admin/licenses",
        headers=h,
        content=empty,
    )
    assert r.status_code == 200
    token = r.json()["token"]
    row = db_session.query(LicenseCode).one()
    assert row.token_enc != token
    assert token not in (row.token_enc or "")
    uses = db_session.query(LicenseCodeUse).count()
    assert uses == 0


def test_admin_routes_require_hmac(client: TestClient, monkeypatch):
    headers, pid, _ = _register(client)
    _as_admin(monkeypatch, pid)
    r = client.get("/api/admin/licenses", headers={"X-Device-Id": pid})
    assert r.status_code == 401
