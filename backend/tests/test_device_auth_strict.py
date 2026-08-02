"""Device HMAC strict mode + legacy forge rejection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import pytest


@pytest.fixture
def da(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    import app.config as cfg

    cfg.get_settings.cache_clear()
    import app.services.device_auth as module
    from importlib import reload

    module = reload(module)
    # reset module globals after reload
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    yield module
    cfg.get_settings.cache_clear()


def _secret_pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    sh = hashlib.sha256(secret).hexdigest()
    pid = "vk_" + sh[:40]
    return secret, b64, pid


def test_legacy_dev_id_rejected_for_hmac(da):
    ok, err = da.verify_request(
        "dev_forged_device_id_12345",
        None,
        None,
        "GET",
        "/api/accounts",
        require_hmac=True,
    )
    assert ok is False
    assert err


def test_legacy_ok_for_quota_only(da):
    ok, err = da.verify_request(
        "dev_forged_device_id_12345",
        None,
        None,
        "POST",
        "/api/fetch/proxy",
        require_hmac=False,
    )
    assert ok is True


def test_register_and_hmac(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    assert out.startswith("vk_")
    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "GET",
        "/api/accounts",
        require_hmac=True,
    )
    assert ok is True, err


def test_bad_signature_rejected(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ok, err = da.verify_request(
        out,
        str(int(time.time())),
        "0" * 64,
        "GET",
        "/api/accounts",
        require_hmac=True,
    )
    assert ok is False


def test_register_rejects_takeover(da):
    """Cannot overwrite an existing device with a different secret."""
    secret1, b64_1, pid1 = _secret_pair()
    out1 = da.register_device_secret(pid1, b64_1)
    assert out1 == pid1
    # Same id, different secret
    secret2 = os.urandom(32)
    b64_2 = base64.urlsafe_b64encode(secret2).decode().rstrip("=")
    # attacker tries to bind victim public id to attacker's secret
    with pytest.raises(ValueError, match="already registered|does not match"):
        # public_id from secret2 won't match pid1, so "does not match"
        da.register_device_secret(pid1, b64_2)


def test_register_rejects_alias_mismatch(da):
    secret, b64, pid = _secret_pair()
    # wrong public_id (forged victim id)
    victim = "vk_" + "a" * 40
    with pytest.raises(ValueError, match="does not match"):
        da.register_device_secret(victim, b64)
    # genuine register still works
    assert da.register_device_secret(pid, b64) == pid
