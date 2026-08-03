"""Transfer security regressions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.routers import transfer


@pytest.fixture
def vault_device(client: TestClient, monkeypatch, tmp_path):
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

    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    out = da.register_device_secret(pid, b64)

    def headers(method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
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
            "X-Device-Id": out,
            "X-Device-Ts": ts,
            "X-Device-Sign": sig,
            "X-Device-Body-Sha256": body_hash,
        }

    yield headers, out, secret
    cfg.get_settings.cache_clear()


def _register_second(secret: bytes | None = None) -> tuple[str, bytes]:
    import app.services.device_auth as da

    secret = secret or os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    out = da.register_device_secret(pid, b64)
    return out, secret


def _headers_for(
    secret: bytes,
    pid: str,
    method: str,
    path: str,
    body: bytes | str | None = None,
) -> dict[str, str]:
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
        "X-Device-Id": pid,
        "X-Device-Ts": ts,
        "X-Device-Sign": sig,
        "X-Device-Body-Sha256": body_hash,
    }


def test_public_transfer_status_hides_guest_device():
    with transfer._LOCK:
        transfer._PACKAGES.clear()
    created = transfer.create_package(
        transfer.CreateBody(
            blob="opaque-ciphertext",
            host_device_id="host-device",
            direction="to_guest",
        ),
        device_id="host-device",
    )
    transfer.claim_package(
        transfer.ClaimBody(code=created.code, guest_device_id="guest-device"),
        device_id="guest-device",
    )

    # Host with claim_token sees guest
    host = transfer.package_status(
        created.code, created.claim_token, device_id="host-device"
    )
    # Host without token still sees guest (is_host)
    host_no_token = transfer.package_status(created.code, "", device_id="host-device")
    # Guest sees status but not guest id (only host/token)
    guest = transfer.package_status(created.code, "", device_id="guest-device")
    # Wrong device cannot poll details
    with pytest.raises(Exception) as ei:
        transfer.package_status(created.code, "wrong-token", device_id="other-device")
    assert getattr(ei.value, "status_code", None) == 404 or "Not found" in str(ei.value)

    assert host.guest_device_id == "guest-device"
    assert host_no_token.guest_device_id == "guest-device"
    assert guest.guest_device_id is None
    assert guest.status == "claimed"


def test_transfer_create_rejects_anonymous(client: TestClient):
    r = client.post(
        "/api/transfer/create",
        json={
            "blob": "opaque-ciphertext",
            "host_device_id": "host-device",
            "direction": "to_guest",
        },
    )
    assert r.status_code == 401


def test_transfer_status_requires_hmac(client: TestClient, vault_device):
    headers_fn, pid, _secret = vault_device
    create_body = {
        "blob": "opaque-ciphertext",
        "host_device_id": pid,
        "direction": "to_guest",
        "label": "snapshot",
    }
    body_bytes = json.dumps(create_body).encode("utf-8")
    create = client.post(
        "/api/transfer/create",
        headers={**headers_fn("POST", "/api/transfer/create", body_bytes), "Content-Type": "application/json"},
        content=body_bytes,
    )
    assert create.status_code == 200
    code = create.json()["code"]

    anon = client.get(f"/api/transfer/status/{code}")
    assert anon.status_code == 401


def test_transfer_status_rejects_non_participant(client: TestClient, vault_device):
    headers_fn, pid, secret = vault_device
    create_body = {
        "blob": "opaque-ciphertext",
        "host_device_id": pid,
        "direction": "to_guest",
        "label": "secret-label",
    }
    body_bytes = json.dumps(create_body).encode("utf-8")
    create = client.post(
        "/api/transfer/create",
        headers={**headers_fn("POST", "/api/transfer/create", body_bytes), "Content-Type": "application/json"},
        content=body_bytes,
    )
    assert create.status_code == 200
    code = create.json()["code"]

    other_pid, other_secret = _register_second()
    path = f"/api/transfer/status/{code}"
    other_headers = _headers_for(other_secret, other_pid, "GET", path)
    r = client.get(path, headers=other_headers)
    assert r.status_code == 404


def test_transfer_approve_download_reject_non_participant(client: TestClient, vault_device):
    headers_fn, pid, secret = vault_device
    create_body = {
        "blob": "opaque-ciphertext",
        "host_device_id": pid,
        "direction": "to_guest",
    }
    body_bytes = json.dumps(create_body).encode("utf-8")
    create = client.post(
        "/api/transfer/create",
        headers={**headers_fn("POST", "/api/transfer/create", body_bytes), "Content-Type": "application/json"},
        content=body_bytes,
    )
    assert create.status_code == 200
    created = create.json()

    guest_pid, guest_secret = _register_second()
    claim_body = {"code": created["code"], "guest_device_id": guest_pid}
    claim_bytes = json.dumps(claim_body).encode("utf-8")
    claimed = client.post(
        "/api/transfer/claim",
        headers={
            **_headers_for(guest_secret, guest_pid, "POST", "/api/transfer/claim", claim_bytes),
            "Content-Type": "application/json",
        },
        content=claim_bytes,
    )
    assert claimed.status_code == 200

    stranger_pid, stranger_secret = _register_second()
    approve_body = {
        "code": created["code"],
        "claim_token": created["claim_token"],
        "host_device_id": stranger_pid,
        "approve": True,
    }
    approve_bytes = json.dumps(approve_body).encode("utf-8")
    bad_approve = client.post(
        "/api/transfer/approve",
        headers={
            **_headers_for(
                stranger_secret, stranger_pid, "POST", "/api/transfer/approve", approve_bytes
            ),
            "Content-Type": "application/json",
        },
        content=approve_bytes,
    )
    assert bad_approve.status_code == 403

    # Real host approves so download path reaches device-mismatch check (403).
    good_approve_body = {
        "code": created["code"],
        "claim_token": created["claim_token"],
        "host_device_id": pid,
        "approve": True,
    }
    good_approve_bytes = json.dumps(good_approve_body).encode("utf-8")
    ok_approve = client.post(
        "/api/transfer/approve",
        headers={
            **headers_fn("POST", "/api/transfer/approve", good_approve_bytes),
            "Content-Type": "application/json",
        },
        content=good_approve_bytes,
    )
    assert ok_approve.status_code == 200

    download_body = {"code": created["code"], "guest_device_id": stranger_pid}
    download_bytes = json.dumps(download_body).encode("utf-8")
    bad_dl = client.post(
        "/api/transfer/download",
        headers={
            **_headers_for(
                stranger_secret, stranger_pid, "POST", "/api/transfer/download", download_bytes
            ),
            "Content-Type": "application/json",
        },
        content=download_bytes,
    )
    assert bad_dl.status_code == 403


def test_transfer_full_flow_with_device_auth(client: TestClient, vault_device):
    headers_fn, pid, _secret = vault_device
    create_body = {
        "blob": "opaque-ciphertext",
        "host_device_id": pid,
        "direction": "to_guest",
        "label": "snapshot",
    }
    body_bytes = json.dumps(create_body).encode("utf-8")
    create = client.post(
        "/api/transfer/create",
        headers={**headers_fn("POST", "/api/transfer/create", body_bytes), "Content-Type": "application/json"},
        content=body_bytes,
    )
    assert create.status_code == 200
    created = create.json()

    claim_body = {"code": created["code"], "guest_device_id": pid}
    claim_bytes = json.dumps(claim_body).encode("utf-8")
    claimed = client.post(
        "/api/transfer/claim",
        headers={**headers_fn("POST", "/api/transfer/claim", claim_bytes), "Content-Type": "application/json"},
        content=claim_bytes,
    )
    assert claimed.status_code == 200
    claim = claimed.json()

    approve_body = {
        "code": created["code"],
        "claim_token": claim["claim_token"],
        "host_device_id": pid,
        "approve": True,
    }
    approve_bytes = json.dumps(approve_body).encode("utf-8")
    approved = client.post(
        "/api/transfer/approve",
        headers={**headers_fn("POST", "/api/transfer/approve", approve_bytes), "Content-Type": "application/json"},
        content=approve_bytes,
    )
    assert approved.status_code == 200

    download_body = {"code": created["code"], "guest_device_id": pid}
    download_bytes = json.dumps(download_body).encode("utf-8")
    downloaded = client.post(
        "/api/transfer/download",
        headers={**headers_fn("POST", "/api/transfer/download", download_bytes), "Content-Type": "application/json"},
        content=download_bytes,
    )
    assert downloaded.status_code == 200
    assert downloaded.json()["blob"] == "opaque-ciphertext"
