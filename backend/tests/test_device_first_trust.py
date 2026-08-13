"""First-trust device admission: first device auto-trusted, later ones pending."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient


_EMPTY = hashlib.sha256(b"").hexdigest()


@pytest.fixture
def da(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    monkeypatch.setenv("OPENMAIL_DEVICE_ADMISSION", "first_trust")
    import app.config as cfg

    cfg.get_settings.cache_clear()
    import app.services.device_auth as module
    from importlib import reload

    module = reload(module)
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    module._status.clear()
    module._created_at.clear()
    if hasattr(module, "_seen_hmac"):
        module._seen_hmac.clear()
    if hasattr(module, "_register_by_ip"):
        module._register_by_ip.clear()
    yield module
    cfg.get_settings.cache_clear()


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    sh = hashlib.sha256(secret).hexdigest()
    return secret, b64, "vk_" + sh[:40]


def _sign(secret: bytes, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    msg = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return {
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
        "X-Device-Body-Sha256": body_hash,
    }


def test_first_device_is_trusted(da):
    secret, b64, pid = _pair()
    assert da.register_device_secret(pid, b64) == pid
    assert da.device_status(pid) == "trusted"

    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(pid, ts, sig, "GET", "/api/accounts")
    assert ok is True, err


def test_second_device_is_pending_and_cannot_use_apis(da):
    s1, b1, p1 = _pair()
    s2, b2, p2 = _pair()
    da.register_device_secret(p1, b1)
    da.register_device_secret(p2, b2)

    assert da.device_status(p1) == "trusted"
    assert da.device_status(p2) == "pending"

    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts".encode()
    sig = hmac.new(s2, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(p2, ts, sig, "GET", "/api/accounts")
    assert ok is False
    assert "pending" in (err or "").lower()

    # HMAC itself is fine when trusted is not required.
    ok2, err2 = da.verify_request(
        p2, ts, sig, "GET", "/api/accounts", require_trusted=False
    )
    assert ok2 is True, err2


def test_trusted_device_can_approve_pending(da):
    s1, b1, p1 = _pair()
    s2, b2, p2 = _pair()
    da.register_device_secret(p1, b1)
    da.register_device_secret(p2, b2)

    assert da.approve_device(p2, actor_id=p1) == "trusted"
    assert da.device_status(p2) == "trusted"

    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts".encode()
    sig = hmac.new(s2, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(p2, ts, sig, "GET", "/api/accounts")
    assert ok is True, err


def test_pending_cannot_approve(da):
    _, b1, p1 = _pair()
    _, b2, p2 = _pair()
    _, b3, p3 = _pair()
    da.register_device_secret(p1, b1)
    da.register_device_secret(p2, b2)
    da.register_device_secret(p3, b3)

    with pytest.raises(ValueError, match="trusted"):
        da.approve_device(p3, actor_id=p2)


def test_cannot_revoke_last_trusted(da):
    _, b1, p1 = _pair()
    da.register_device_secret(p1, b1)
    with pytest.raises(ValueError, match="last trusted"):
        da.revoke_device(p1, actor_id=p1)


def test_legacy_registry_entries_without_status_are_trusted(da, tmp_path, monkeypatch):
    """Upgrades must not lock out devices that registered under open admission."""
    s1, b1, p1 = _pair()
    # Write a v1-shaped registry (no status field) the way older builds did.
    from app.crypto import encrypt_str

    secret_b64 = b1
    enc = encrypt_str(secret_b64)
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "v": 1,
                "entries": [
                    {"public_id": p1, "secret_enc": enc, "hash": hashlib.sha256(s1).hexdigest()}
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(path))
    da._loaded = False
    da._secrets.clear()
    da._registry.clear()
    da._status.clear()
    da._created_at.clear()

    assert da.device_status(p1) == "trusted"
    # A brand-new second device still waits for approval.
    _, b2, p2 = _pair()
    da.register_device_secret(p2, b2)
    assert da.device_status(p2) == "pending"


def test_open_admission_trusts_every_register(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    monkeypatch.setenv("OPENMAIL_DEVICE_ADMISSION", "open")
    import app.config as cfg
    from importlib import reload
    import app.services.device_auth as module

    cfg.get_settings.cache_clear()
    module = reload(module)
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    module._status.clear()
    module._created_at.clear()

    _, b1, p1 = _pair()
    _, b2, p2 = _pair()
    module.register_device_secret(p1, b1)
    module.register_device_secret(p2, b2)
    assert module.device_status(p1) == "trusted"
    assert module.device_status(p2) == "trusted"
    cfg.get_settings.cache_clear()


def test_register_and_approve_over_http(tmp_path, monkeypatch):
    """End-to-end through the FastAPI app, with a private registry file."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("OPENMAIL_DEVICE_ADMISSION", "first_trust")
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "http-reg.json"))
    monkeypatch.setenv("SYNC_ENABLED_GLOBAL", "false")

    import app.config as cfg
    from importlib import reload
    import app.services.device_auth as module
    from app.db import Base, get_db
    from app.main import create_app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import app.db as app_db
    import app.models  # noqa: F401

    cfg.get_settings.cache_clear()
    module = reload(module)
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    module._status.clear()
    module._created_at.clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    app_db.engine = engine
    app_db.SessionLocal = TestingSessionLocal

    def _override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    application = create_app()
    application.dependency_overrides[get_db] = _override_db

    s1, b1, p1 = _pair()
    s2, b2, p2 = _pair()

    with TestClient(application) as c:
        r1 = c.post("/api/device/register", json={"public_id": p1, "secret_b64": b1})
        assert r1.status_code == 200
        assert r1.json()["status"] == "trusted"

        r2 = c.post("/api/device/register", json={"public_id": p2, "secret_b64": b2})
        assert r2.status_code == 200
        assert r2.json()["status"] == "pending"

        me = c.get(
            "/api/device/me",
            headers={"X-Device-Id": p2, **_sign(s2, "GET", "/api/device/me")},
        )
        assert me.status_code == 200
        assert me.json()["status"] == "pending"

        bad_list = c.get(
            "/api/device/list",
            headers={"X-Device-Id": p2, **_sign(s2, "GET", "/api/device/list")},
        )
        assert bad_list.status_code == 401

        approve_body = json.dumps({"public_id": p2}).encode()
        ok = c.post(
            "/api/device/approve",
            headers={
                "Content-Type": "application/json",
                "X-Device-Id": p1,
                **_sign(s1, "POST", "/api/device/approve", approve_body),
            },
            content=approve_body,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "trusted"

        good_list = c.get(
            "/api/device/list",
            headers={"X-Device-Id": p2, **_sign(s2, "GET", "/api/device/list")},
        )
        assert good_list.status_code == 200
        assert len(good_list.json()["devices"]) == 2

    application.dependency_overrides.clear()
    cfg.get_settings.cache_clear()


def test_pending_cap_rejects_further_devices(da, monkeypatch):
    monkeypatch.setattr(da, "MAX_PENDING_DEVICES", 1)
    _, b1, p1 = _pair()
    _, b2, p2 = _pair()
    _, b3, p3 = _pair()
    da.register_device_secret(p1, b1)
    da.register_device_secret(p2, b2)
    with pytest.raises(ValueError, match="pending"):
        da.register_device_secret(p3, b3)


def test_register_http_rate_limited_per_ip(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("OPENMAIL_DEVICE_ADMISSION", "open")
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "rate-reg.json"))
    monkeypatch.setenv("SYNC_ENABLED_GLOBAL", "false")

    import app.config as cfg
    from importlib import reload
    import app.services.device_auth as module
    from app.db import Base, get_db
    from app.main import create_app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import app.db as app_db
    import app.models  # noqa: F401

    cfg.get_settings.cache_clear()
    module = reload(module)
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    module._status.clear()
    module._created_at.clear()
    module._register_by_ip.clear()
    monkeypatch.setattr(module, "REGISTER_MAX_PER_IP", 2)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    app_db.engine = engine
    app_db.SessionLocal = TestingSessionLocal

    def _override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    application = create_app()
    application.dependency_overrides[get_db] = _override_db

    with TestClient(application) as c:
        codes = []
        for _ in range(4):
            _, b64, pid = _pair()
            r = c.post("/api/device/register", json={"public_id": pid, "secret_b64": b64})
            codes.append(r.status_code)
        assert codes[:2] == [200, 200]
        assert codes[2:] == [429, 429]

    application.dependency_overrides.clear()
    cfg.get_settings.cache_clear()
