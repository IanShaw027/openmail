"""OPENMAIL_INSTANCE_MODE=solo|hosted and first-trusted-device admin bootstrap."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.license import check_poll_quota, is_licensed, quota_snapshot


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    return secret, b64, pid


def _sign(secret: bytes, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    body_hash = hashlib.sha256(body).hexdigest()
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    msg = f"{ts}.{method.upper()}.{path}.{body_hash}.{nonce}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return {
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
        "X-Device-Body-Sha256": body_hash,
        "X-Device-Nonce": nonce,
    }


def _register(client: TestClient):
    secret, b64, pid = _pair()
    r = client.post("/api/device/register", json={"public_id": pid, "secret_b64": b64})
    assert r.status_code == 200, r.text
    out = r.json()["public_id"]

    def headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
        h = {"X-Device-Id": out}
        h.update(_sign(secret, method, path, body))
        return h

    return headers, out, r.json()["status"]


def test_instance_mode_defaults_to_hosted():
    get_settings.cache_clear()
    assert get_settings().instance_mode == "hosted"


def test_solo_mode_is_licensed_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAIL_INSTANCE_MODE", "solo")
    monkeypatch.setenv("OPENMAIL_ADMIN_DEVICE_IDS", "")
    monkeypatch.setenv("OPENMAIL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    assert get_settings().instance_mode == "solo"
    from app.services.settings_service import get_effective_settings

    assert get_effective_settings().sync_enabled_global is False
    assert is_licensed(device_id="vk_anyone", license_token=None) is True
    snap = quota_snapshot(device_id="vk_anyone")
    assert snap["licensed"] is True
    assert snap["max_local_accounts"] is None
    ok, err = check_poll_quota("vk_anyone", license_token=None)
    assert ok is True, err
    get_settings.cache_clear()


def test_solo_forces_open_admission(monkeypatch):
    monkeypatch.setenv("OPENMAIL_INSTANCE_MODE", "solo")
    monkeypatch.setenv("OPENMAIL_DEVICE_ADMISSION", "first_trust")
    get_settings.cache_clear()
    from app.services import device_auth as da

    assert da._admission_mode() == "open"
    get_settings.cache_clear()


def test_first_trusted_device_becomes_admin(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMAIL_ADMIN_DEVICE_IDS", "")
    monkeypatch.setenv("OPENMAIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENMAIL_INSTANCE_MODE", "hosted")
    monkeypatch.delenv("OPENMAIL_ENV_FILE", raising=False)
    get_settings.cache_clear()

    headers, pid, status = _register(client)
    assert status == "trusted"
    r = client.get("/api/device/me", headers=headers("GET", "/api/device/me"))
    assert r.status_code == 200
    assert r.json()["is_admin"] is True

    persisted = (tmp_path / "admin_device_ids").read_text(encoding="utf-8")
    assert pid in persisted

    headers2, pid2, status2 = _register(client)
    assert status2 in ("trusted", "pending")
    r2 = client.get("/api/device/me", headers=headers2("GET", "/api/device/me"))
    if r2.status_code == 200:
        assert r2.json()["is_admin"] is False
        assert r2.json()["public_id"] == pid2


def test_env_admin_overrides_persisted_file(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMAIL_DATA_DIR", str(tmp_path))
    (tmp_path / "admin_device_ids").write_text("vk_from_file_should_not_win_xxxxxxxx\n", encoding="utf-8")
    headers, pid, _ = _register(client)
    monkeypatch.setenv("OPENMAIL_ADMIN_DEVICE_IDS", "vk_operator_recovery_id_xxxxxxxxxxxx")
    get_settings.cache_clear()
    r = client.get("/api/device/me", headers=headers("GET", "/api/device/me"))
    assert r.status_code == 200
    assert r.json()["is_admin"] is False


def test_public_config_exposes_instance_mode(client: TestClient, monkeypatch):
    monkeypatch.setenv("OPENMAIL_INSTANCE_MODE", "solo")
    get_settings.cache_clear()
    r = client.get("/api/config/public")
    assert r.status_code == 200
    body = r.json()
    assert body["instance_mode"] == "solo"
    assert body["license_ui_enabled"] is False
    assert body["sync_ui_enabled"] is False
    get_settings.cache_clear()
