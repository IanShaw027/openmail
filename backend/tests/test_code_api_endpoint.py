"""End-to-end checks for /api/v1/code/{token}.

The quota unit tests call the service directly, which leaves the wiring
unverified: whether the limiter runs before the upstream fetch, whether it
surfaces as a 429, and whether an unknown token is throttled at all. Those are
the parts an attacker actually touches, so exercise them through the app.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.models import Account, CodeApiToken, ProviderType
from app.services.fetch_service import FetchServiceResult


@pytest.fixture()
def token_client(client: TestClient, monkeypatch):
    """A client with one enabled and one disabled token, and no real upstream."""
    factory = client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        # One account per token: code_api_tokens is unique on account_id.
        live = Account(email="live@example.com", provider=ProviderType.imap)
        off = Account(email="off@example.com", provider=ProviderType.imap)
        db.add_all([live, off])
        db.flush()
        db.add(CodeApiToken(token="tok_live", account_id=live.id, enabled=True))
        db.add(CodeApiToken(token="tok_off", account_id=off.id, enabled=False))
        db.commit()
    finally:
        db.close()

    calls: list[str] = []

    def _fake_fetch(db_, acc_, **kwargs):
        calls.append(acc_.email)
        return FetchServiceResult(ok=True, code="123456", email=acc_.email)

    monkeypatch.setattr("app.routers.code_api.fetch_account", _fake_fetch)
    client.upstream_calls = calls  # type: ignore[attr-defined]
    return client


def _set_limits(monkeypatch, *, fetch: str, refresh: str = "15") -> None:
    from app.config import get_settings

    monkeypatch.setitem(os.environ, "CODE_API_MAX_FETCH_PER_HOUR", fetch)
    monkeypatch.setitem(os.environ, "CODE_API_MAX_REFRESH_PER_HOUR", refresh)
    get_settings.cache_clear()


def test_unknown_token_is_404_and_eventually_429(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="3")

    codes = [token_client.get(f"/api/v1/code/nope{i}").status_code for i in range(5)]

    # Misses are throttled by client IP, so token enumeration hits the wall too.
    assert codes[:3] == [404, 404, 404]
    assert codes[3:] == [429, 429]
    assert token_client.upstream_calls == []  # type: ignore[attr-defined]


def test_disabled_token_is_throttled_like_a_miss(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="2")

    assert token_client.get("/api/v1/code/tok_off").status_code == 404
    assert token_client.get("/api/v1/code/tok_off").status_code == 404
    assert token_client.get("/api/v1/code/tok_off").status_code == 429


def test_live_token_is_capped_and_stops_calling_upstream(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="4")

    codes = [token_client.get("/api/v1/code/tok_live").status_code for _ in range(6)]

    assert codes == [200, 200, 200, 200, 429, 429]
    # The limiter runs before the fetch, so rejected requests cost nothing upstream.
    assert len(token_client.upstream_calls) == 4  # type: ignore[attr-defined]


def test_rate_limited_response_tells_the_client_when_to_retry(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="1")

    assert token_client.get("/api/v1/code/tok_live").status_code == 200
    resp = token_client.get("/api/v1/code/tok_live")

    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"


def test_zero_means_unlimited_over_the_wire(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="0", refresh="0")

    codes = [token_client.get("/api/v1/code/tok_live").status_code for _ in range(70)]

    assert set(codes) == {200}


def test_text_format_returns_the_bare_code(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="0")

    resp = token_client.get("/api/v1/code/tok_live", params={"format": "text"})

    assert resp.status_code == 200
    assert resp.text == "123456"


def test_token_default_keyword_and_regex_are_passed_to_fetch(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="0")
    factory = token_client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        row = db.query(CodeApiToken).filter(CodeApiToken.token == "tok_live").one()
        row.default_keyword = "PayPal"
        row.default_regex = r"\d{6}"
        db.commit()
    finally:
        db.close()

    captured: list[dict] = []

    def _fake_fetch(db_, acc_, **kwargs):
        captured.append(kwargs)
        return FetchServiceResult(ok=True, code="999000", email=acc_.email)

    monkeypatch.setattr("app.routers.code_api.fetch_account", _fake_fetch)
    resp = token_client.get("/api/v1/code/tok_live")
    assert resp.status_code == 200
    assert captured and captured[0].get("keyword") == "PayPal"
    assert captured[0].get("custom_regex") == r"\d{6}"
    assert captured[0].get("folder") == "inbox"


def test_query_keyword_overrides_token_default(token_client, monkeypatch):
    _set_limits(monkeypatch, fetch="0")
    factory = token_client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        row = db.query(CodeApiToken).filter(CodeApiToken.token == "tok_live").one()
        row.default_keyword = "PayPal"
        db.commit()
    finally:
        db.close()

    captured: list[dict] = []

    def _fake_fetch(db_, acc_, **kwargs):
        captured.append(kwargs)
        return FetchServiceResult(ok=True, code="111222", email=acc_.email)

    monkeypatch.setattr("app.routers.code_api.fetch_account", _fake_fetch)
    resp = token_client.get("/api/v1/code/tok_live", params={"keyword": "Amazon", "folder": "junk"})
    assert resp.status_code == 200
    assert captured[0].get("keyword") == "Amazon"
    assert captured[0].get("folder") == "junk"


def test_query_regex_is_ignored_public_code_api(token_client, monkeypatch):
    """Caller-supplied regex is not executed; only the token's stored pattern is."""
    _set_limits(monkeypatch, fetch="0")
    factory = token_client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        row = db.query(CodeApiToken).filter(CodeApiToken.token == "tok_live").one()
        row.default_regex = r"\d{6}"
        db.commit()
    finally:
        db.close()

    captured: list[dict] = []

    def _fake_fetch(db_, acc_, **kwargs):
        captured.append(kwargs)
        return FetchServiceResult(ok=True, code="111222", email=acc_.email)

    monkeypatch.setattr("app.routers.code_api.fetch_account", _fake_fetch)
    resp = token_client.get(
        "/api/v1/code/tok_live",
        params={"regex": r"(a+)+$"},
    )
    assert resp.status_code == 200
    assert captured[0].get("custom_regex") == r"\d{6}"


def test_hmac_disable_stops_public_fetch(client, monkeypatch, tmp_path):
    import base64
    import hashlib
    import hmac
    import time

    from app.config import get_settings
    from app.models import Account, CodeApiToken, ProviderType

    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", key)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    get_settings.cache_clear()
    import app.services.device_auth as da
    from importlib import reload

    da = reload(da)
    da._loaded = False
    da._secrets.clear()
    da._registry.clear()
    da._status.clear()
    da._created_at.clear()

    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    da.register_device_secret(pid, b64)

    factory = client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        acc = Account(
            email="owned@example.com",
            provider=ProviderType.imap,
            owner_user_id=pid,
        )
        db.add(acc)
        db.flush()
        db.add(CodeApiToken(token="tok_owned", account_id=acc.id, enabled=True))
        db.commit()
        acc_id = acc.id
    finally:
        db.close()

    def _sign(method: str, path: str, body: bytes = b"") -> dict[str, str]:
        body_hash = hashlib.sha256(body).hexdigest()
        ts = str(int(time.time()))
        msg = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
        sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        return {
            "X-Device-Id": pid,
            "X-Device-Ts": ts,
            "X-Device-Sign": sig,
            "X-Device-Body-Sha256": body_hash,
        }

    path = f"/api/accounts/{acc_id}/code-api/disable"
    r = client.post(path, headers=_sign("POST", path))
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False

    miss = client.get("/api/v1/code/tok_owned")
    assert miss.status_code == 404

