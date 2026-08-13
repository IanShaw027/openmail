"""Device-scoped GET /api/sync/status and /api/sync/delta."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import Account, AccountPool, AccountStatus, MailItem, ProviderType
from app.providers.base import Message
from app.services.mail_store import list_delta, upsert_messages


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
    da._status.clear()
    da._created_at.clear()

    secret, b64, pid = _pair()
    out = da.register_device_secret(pid, b64)

    def headers(method: str, path: str, body: bytes | str | None = None) -> dict[str, str]:
        h = {"X-Device-Id": out}
        h.update(_sign(secret, method, path, body))
        return h

    yield headers, out
    cfg.get_settings.cache_clear()


def _make_account(
    db,
    *,
    owner: str,
    email: str = "sync@example.com",
    sync_enabled: bool = True,
) -> Account:
    acc = Account(
        email=email,
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
        owner_user_id=owner,
        status=AccountStatus.ok,
        sync_enabled=sync_enabled,
        latest_verification_code="123456",
        latest_code_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def test_openapi_has_sync_routes(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/sync/status" in paths
    assert "get" in paths["/api/sync/status"]
    assert "/api/sync/delta" in paths
    assert "get" in paths["/api/sync/delta"]
    assert paths["/api/me/sync"]["post"]


def test_me_sync_still_410(client: TestClient):
    r = client.post("/api/me/sync")
    assert r.status_code == 410


def test_sync_status_requires_hmac(client: TestClient):
    r = client.get("/api/sync/status", headers={"X-Device-Id": "dev_forged_xx"})
    assert r.status_code == 401


def test_sync_delta_requires_hmac(client: TestClient):
    r = client.get("/api/sync/delta")
    assert r.status_code in (400, 401)


def test_sync_status_ok(client: TestClient, db_session, vault_device):
    headers_fn, device_id = vault_device
    acc = _make_account(db_session, owner=device_id, email="a@ex.com", sync_enabled=True)
    # account with mail but sync off should still appear
    acc2 = _make_account(
        db_session, owner=device_id, email="b@ex.com", sync_enabled=False
    )
    settings = get_settings()
    upsert_messages(
        db_session,
        acc2.id,
        "inbox",
        [Message(id="m1", subject="Hi", from_="x@y.z")],
        settings=settings,
    )
    # other device must not appear
    _make_account(db_session, owner="vk_other_device", email="other@ex.com")
    db_session.commit()

    path = "/api/sync/status"
    r = client.get(path, headers=headers_fn("GET", path))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "worker_alive" in body
    assert isinstance(body["worker_alive"], bool)
    assert "sync_enabled_global" in body
    assert isinstance(body["sync_enabled_global"], bool)
    emails = {a["email"] for a in body["accounts"]}
    assert "a@ex.com" in emails
    assert "b@ex.com" in emails
    assert "other@ex.com" not in emails
    by_email = {a["email"]: a for a in body["accounts"]}
    assert by_email["a@ex.com"]["mail_count"] == 0
    assert by_email["b@ex.com"]["mail_count"] == 1
    assert by_email["a@ex.com"]["id"] == acc.id
    assert "client_sealed" in by_email["a@ex.com"]
    assert by_email["a@ex.com"]["sync_enabled"] is True


def test_sync_delta_pulls_device_mails(client: TestClient, db_session, vault_device):
    headers_fn, device_id = vault_device
    settings = get_settings()
    acc = _make_account(db_session, owner=device_id, email="delta@ex.com")
    other = _make_account(db_session, owner="vk_other", email="secret@ex.com")

    base = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
    msgs = [
        Message(
            id=f"d{i}",
            subject=f"s{i}",
            from_="n@e.com",
            date=(base + timedelta(minutes=i)).isoformat(),
            body_preview=f"p{i}",
            body_text=f"full body {i}",
            verification_code="111111" if i == 0 else None,
        )
        for i in range(3)
    ]
    upsert_messages(db_session, acc.id, "inbox", msgs, settings=settings)
    upsert_messages(
        db_session,
        other.id,
        "inbox",
        [Message(id="secret", subject="nope", from_="z@z.z")],
        settings=settings,
    )
    db_session.commit()

    rows = (
        db_session.query(MailItem)
        .filter(MailItem.account_id == acc.id)
        .order_by(MailItem.stable_id.asc())
        .all()
    )
    for i, row in enumerate(rows):
        row.updated_at = base + timedelta(seconds=i)
    db_session.commit()

    path = "/api/sync/delta?limit=2"
    r = client.get(path, headers=headers_fn("GET", path))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_more"] is True
    assert body["server_time"]
    assert body.get("server_seq") is None
    assert len(body["mails"]) == 2
    assert all(m["email"] == "delta@ex.com" for m in body["mails"])
    assert all(m["body_text"] for m in body["mails"])
    assert all(m["preview"] in ("p0", "p1", "p2") for m in body["mails"])
    codes = {m["verification_code"] for m in body["mails"]}
    assert "111111" in codes
    path_omit = "/api/sync/delta?limit=2&include_body=false"
    r_omit = client.get(path_omit, headers=headers_fn("GET", path_omit))
    assert r_omit.status_code == 200, r_omit.text
    assert all(m["body_text"] is None for m in r_omit.json()["mails"])
    assert all(m["body_html"] is None for m in r_omit.json()["mails"])
    # accounts meta includes sync_enabled for this device
    acc_ids = {a["id"] for a in body["accounts"]}
    assert acc.id in acc_ids
    meta = next(a for a in body["accounts"] if a["id"] == acc.id)
    assert meta["latest_verification_code"] == "123456"
    assert meta["email"] == "delta@ex.com"

    last = body["mails"][-1]
    path2 = f"/api/sync/delta?since={last['updated_at']}&since_id={last['id']}&limit=2"
    r2 = client.get(path2, headers=headers_fn("GET", path2))
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["mails"]) == 1
    assert body2["has_more"] is False
    ids1 = {m["id"] for m in body["mails"]}
    ids2 = {m["id"] for m in body2["mails"]}
    assert ids1.isdisjoint(ids2)


def test_list_delta_integration_matches_api_shape(db_session):
    """DB-only check that list_delta feeds the API response fields."""
    device = "vk_delta_shape"
    settings = get_settings()
    acc = _make_account(db_session, owner=device)
    upsert_messages(
        db_session,
        acc.id,
        "inbox",
        [Message(id="x1", subject="Code", from_="a@b.c", verification_code="999")],
        settings=settings,
    )
    db_session.commit()
    out = list_delta(db_session, device, since=None, limit=10, settings=settings)
    assert out["has_more"] is False
    assert out["server_time"]
    assert len(out["mails"]) == 1
    m = out["mails"][0]
    for key in (
        "account_id",
        "email",
        "folder",
        "stable_id",
        "id",
        "subject",
        "from_addr",
        "to_addrs",
        "date",
        "preview",
        "verification_code",
        "body_text",
        "body_html",
        "updated_at",
        "deleted",
    ):
        assert key in m
