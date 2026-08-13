"""Device HMAC strict mode + legacy forge rejection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import pytest

_EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


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
    module._status.clear()
    module._created_at.clear()
    if hasattr(module, "_seen_hmac"):
        module._seen_hmac.clear()
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
    """GET without body hash: legacy path-only signature still accepted."""
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


def test_register_and_hmac_with_body_hash(da):
    """New format: {ts}.{METHOD}.{path}.{body_sha256}."""
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    body = b'{"a":1}'
    body_hash = hashlib.sha256(body).hexdigest()
    msg = f"{ts}.POST./api/fetch/send.{body_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "POST",
        "/api/fetch/send",
        require_hmac=True,
        body_sha256=body_hash,
    )
    assert ok is True, err


def test_get_query_string_is_signed(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    path = "/api/accounts?folder=spam&quick=false"
    msg = f"{ts}.GET.{path}.{_EMPTY_BODY_SHA256}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "GET",
        path,
        require_hmac=True,
        body_sha256=_EMPTY_BODY_SHA256,
    )
    assert ok is True, err


def test_get_with_empty_body_hash(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts.{_EMPTY_BODY_SHA256}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "GET",
        "/api/accounts",
        require_hmac=True,
        body_sha256=_EMPTY_BODY_SHA256,
    )
    assert ok is True, err


def test_post_requires_body_hash(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    # Legacy path-only signature must not work for POST
    msg = f"{ts}.POST./api/fetch/send".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "POST",
        "/api/fetch/send",
        require_hmac=True,
        body_sha256=None,
    )
    assert ok is False
    assert err and "Body-Sha256" in err


def test_wrong_body_hash_signature_rejected(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    real_hash = hashlib.sha256(b'{"a":1}').hexdigest()
    wrong_hash = hashlib.sha256(b'{"a":2}').hexdigest()
    msg = f"{ts}.POST./api/x.{real_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "POST",
        "/api/x",
        require_hmac=True,
        body_sha256=wrong_hash,
    )
    assert ok is False


def test_delete_requires_body_sha256(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    # Legacy path-only DELETE must fail
    msg = f"{ts}.DELETE./api/accounts/x".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "DELETE",
        "/api/accounts/x",
        require_hmac=True,
        body_sha256=None,
    )
    assert ok is False
    assert err and "Body-Sha256" in err

    # Empty-body hash is accepted
    empty = hashlib.sha256(b"").hexdigest()
    msg2 = f"{ts}.DELETE./api/accounts/x.{empty}".encode()
    sig2 = hmac.new(secret, msg2, hashlib.sha256).hexdigest()
    ok2, err2 = da.verify_request(
        out,
        ts,
        sig2,
        "DELETE",
        "/api/accounts/x",
        require_hmac=True,
        body_sha256=empty,
    )
    assert ok2 is True, err2


def test_invalid_body_sha256_format_rejected(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    msg = f"{ts}.POST./api/x.not-a-hash".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    ok, err = da.verify_request(
        out,
        ts,
        sig,
        "POST",
        "/api/x",
        require_hmac=True,
        body_sha256="not-a-hash",
    )
    assert ok is False
    assert err and "Body-Sha256" in err


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


def test_unknown_device_reloads_registry_written_by_another_worker(da):
    _, b64, pid = _secret_pair()
    da.register_device_secret(pid, b64)
    da._secrets.clear()
    da._registry.clear()
    da._status.clear()
    da._created_at.clear()
    da._loaded = True
    assert da.is_registered(pid) is True


def test_mutating_hmac_replay_rejected(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(b'{"a":1}').hexdigest()
    msg = f"{ts}.POST./api/fetch/send.{body_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    kwargs = dict(
        public_id=out,
        ts=ts,
        signature=sig,
        method="POST",
        path="/api/fetch/send",
        require_hmac=True,
        body_sha256=body_hash,
    )
    ok, err = da.verify_request(**kwargs)
    assert ok is True, err
    ok2, err2 = da.verify_request(**kwargs)
    assert ok2 is False
    assert err2 and "replay" in err2.lower()


def test_pending_mutating_hmac_does_not_count_as_replay(da):
    """A rejected pending POST must not consume the anti-replay slot.

    Recording the signature before the pending check lets an untrusted device
    fill the replay cache (and, with a durable store, the table) then starve
    later legitimate retries of the same key after approval.
    """
    s1, b1, p1 = _secret_pair()
    s2, b2, p2 = _secret_pair()
    da.register_device_secret(p1, b1)
    da.register_device_secret(p2, b2)
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(b"").hexdigest()
    msg = f"{ts}.POST./api/accounts.{body_hash}".encode()
    sig = hmac.new(s2, msg, hashlib.sha256).hexdigest()
    kwargs = dict(
        public_id=p2,
        ts=ts,
        signature=sig,
        method="POST",
        path="/api/accounts",
        require_hmac=True,
        require_trusted=True,
        body_sha256=body_hash,
    )
    ok, err = da.verify_request(**kwargs)
    assert ok is False
    assert err and "pending" in err.lower()
    ok2, err2 = da.verify_request(**kwargs)
    assert ok2 is False
    assert err2 and "pending" in err2.lower()
    assert "replay" not in err2.lower()
    assert not any(p2 in k for k in da._seen_hmac)


def test_hmac_replay_survives_cleared_memory(client):
    """Replay keys must live in the shared DB, not only this process's dict."""
    import app.services.device_auth as da

    secret, b64, pid = _secret_pair()
    assert da.register_device_secret(pid, b64) == pid
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(b'{"a":1}').hexdigest()
    msg = f"{ts}.POST./api/fetch/send.{body_hash}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    kwargs = dict(
        public_id=pid,
        ts=ts,
        signature=sig,
        method="POST",
        path="/api/fetch/send",
        require_hmac=True,
        body_sha256=body_hash,
    )
    ok, err = da.verify_request(**kwargs)
    assert ok is True, err
    da._seen_hmac.clear()
    ok2, err2 = da.verify_request(**kwargs)
    assert ok2 is False
    assert err2 and "replay" in err2.lower()


def test_nonce_allows_same_second_identical_body(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(b'{"a":1}').hexdigest()
    for nonce in ("n1", "n2"):
        msg = f"{ts}.POST./api/fetch/send.{body_hash}.{nonce}".encode()
        sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        ok, err = da.verify_request(
            out,
            ts,
            sig,
            "POST",
            "/api/fetch/send",
            require_hmac=True,
            body_sha256=body_hash,
            nonce=nonce,
        )
        assert ok is True, err


def test_get_hmac_may_repeat_within_skew(da):
    """Reads are not replay-gated; listing twice in the same second must work."""
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ts = str(int(time.time()))
    msg = f"{ts}.GET./api/accounts".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    kwargs = dict(public_id=out, ts=ts, signature=sig, method="GET", path="/api/accounts")
    assert da.verify_request(**kwargs)[0] is True
    assert da.verify_request(**kwargs)[0] is True


def test_hmac_signature_length_mismatch_is_invalid(da):
    secret, b64, pid = _secret_pair()
    out = da.register_device_secret(pid, b64)
    ok, err = da.verify_request(
        out,
        str(int(time.time())),
        "short",
        "GET",
        "/api/accounts",
        require_hmac=True,
    )
    assert ok is False
    assert err
