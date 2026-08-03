"""Tests: proxy sid strategies, settings overrides, sync worker (local-first)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, AccountPool, AccountStatus, ProviderType
from app.providers.base import Message
from app.services.proxy import list_proxy_candidates, resolve_proxy, resolve_sid, sticky_sid
from app.services.settings_service import get_effective_settings, set_overrides
from app.services.sync_worker import get_sync_worker


def test_proxy_sid_sticky_stable() -> None:
    aid = "acc_abcdef123456"
    s1 = sticky_sid(aid)
    s2 = sticky_sid(aid)
    assert s1 == s2
    assert len(s1) == 16
    assert all(c in "0123456789abcdef" for c in s1)

    r1 = resolve_sid(aid, strategy="sticky_per_account")
    r2 = resolve_sid(aid, strategy="sticky_per_account")
    assert r1 == r2 == s1

    # rotate_per_sync should differ across calls (extremely likely)
    a = resolve_sid(aid, strategy="rotate_per_sync")
    b = resolve_sid(aid, strategy="rotate_per_sync")
    assert a != b

    # force_new_sid overrides sticky
    forced = resolve_sid(aid, strategy="sticky_per_account", force_new_sid=True)
    assert forced != s1


def test_resolve_proxy_priority() -> None:
    settings = SimpleNamespace(
        proxy_template="http://user-session-{sid}:pass@host:9000",
        proxy_pool="",
        proxy_sid_strategy="sticky_per_account",
    )
    acc = SimpleNamespace(id="acc_1", proxy="http://explicit:1@p:80", email=None)
    assert resolve_proxy(acc, settings=settings) == "http://explicit:1@p:80"

    acc2 = SimpleNamespace(id="acc_xyz", proxy=None, email=None)
    url = resolve_proxy(acc2, settings=settings)
    assert url is not None
    assert sticky_sid("acc_xyz") in url
    assert url.startswith("http://user-session-")

    settings_empty = SimpleNamespace(
        proxy_template="", proxy_pool="", proxy_sid_strategy="sticky_per_account"
    )
    assert resolve_proxy(acc2, settings=settings_empty) is None


def test_list_proxy_candidates_rotates_pool_then_direct() -> None:
    settings = SimpleNamespace(
        proxy_template="",
        proxy_pool="socks5://warp-1:1080|socks5://warp-2:1080|socks5://warp-3:1080",
        proxy_sid_strategy="sticky_per_account",
    )
    acc = SimpleNamespace(id=None, proxy=None, email="user@mail.com")
    cands = list_proxy_candidates(acc, settings=settings, include_direct=True)
    assert len(cands) == 4  # 3 warp + direct
    assert cands[-1] is None
    assert all(isinstance(x, str) and x.startswith("socks5://warp-") for x in cands[:-1])
    # sticky start is deterministic for same email
    cands2 = list_proxy_candidates(acc, settings=settings, include_direct=True)
    assert cands2[0] == cands[0]
    # without direct
    only = list_proxy_candidates(acc, settings=settings, include_direct=False)
    assert len(only) == 3
    assert None not in only


def test_list_proxy_candidates_fixed_proxy_no_pool_spin() -> None:
    settings = SimpleNamespace(
        proxy_template="",
        proxy_pool="socks5://warp-1:1080|socks5://warp-2:1080",
        proxy_sid_strategy="sticky_per_account",
    )
    acc = SimpleNamespace(id="x", proxy="socks5://fixed:1080", email="a@b.com")
    cands = list_proxy_candidates(acc, settings=settings, include_direct=True)
    assert cands == ["socks5://fixed:1080"]


def test_fetch_account_fixed_proxy_never_falls_back_direct(db_session, monkeypatch) -> None:
    from app.models import AccountPool, AccountStatus, ProviderType
    from app.services.fetch_service import fetch_account

    acc = Account(
        email="proxy@example.com",
        provider=ProviderType.http_api,
        pool=AccountPool.user_private,
        owner_user_id="vk_proxy_device",
        proxy="socks5://fixed:1080",
        status=AccountStatus.ok,
    )
    db_session.add(acc)
    db_session.commit()
    calls: list[dict[str, object]] = []

    def fake_fetch(account, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(
            ok=False,
            messages=[],
            folder=kwargs.get("folder", "inbox"),
            error="proxy failed",
            credential_updates=None,
        )

    def explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.proxy.list_proxy_candidates", explode)
    monkeypatch.setattr("app.services.fetch_service.resolve_provider", lambda account: SimpleNamespace(fetch=fake_fetch))
    result = fetch_account(
        db_session,
        acc,
        settings=SimpleNamespace(
            fetch_min_interval_seconds=0.0,
            fetch_lock_lease_seconds=180.0,
        ),
        force=True,
    )
    assert result.ok is False
    assert calls and calls[0]["credentials"]["proxy"] == "socks5://fixed:1080"


def test_settings_override(db_session: Session) -> None:
    base = get_effective_settings(db_session)
    assert base.sync_interval_seconds == 3600
    assert base.sync_enabled_global is False  # from conftest env

    eff = set_overrides(
        db_session,
        {
            "sync_interval_seconds": 120,
            "proxy_template": "http://u-{sid}:p@h:1",
            "proxy_sid_strategy": "rotate_per_sync",
            "sync_concurrency": 3,
            "sync_enabled_global": True,
        },
    )
    assert eff.sync_interval_seconds == 120
    assert eff.proxy_template == "http://u-{sid}:p@h:1"
    assert eff.proxy_sid_strategy == "rotate_per_sync"
    assert eff.sync_concurrency == 3
    assert eff.sync_enabled_global is True

    # Reload merges from DB
    again = get_effective_settings(db_session)
    assert again.sync_interval_seconds == 120
    assert again.proxy_template == "http://u-{sid}:p@h:1"


def test_sync_one_account_mocked_fetch(db_session: Session) -> None:
    """SyncWorker.sync_one_account with mocked provider (no auth UI)."""
    acc = Account(
        email="sync@example.com",
        provider=ProviderType.http_api,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_owner_001",
        sync_enabled=True,
        status=AccountStatus.ok,
    )
    db_session.add(acc)
    db_session.commit()
    account_id = acc.id

    def _fake_fetch(db, account, **kwargs):  # type: ignore[no-untyped-def]
        from app.services.fetch_service import FetchServiceResult, _write_short_cache

        folder = kwargs.get("folder", "inbox")
        msgs = [
            Message(
                id=f"uid-{folder}",
                subject=f"Hello {folder} 123456",
                from_="bot@x.com",
                from_address="bot@x.com",
                body_text="Your code is 123456",
                folder=folder,
                date=datetime.now(timezone.utc).isoformat(),
                verification_code="123456",
            )
        ]
        _write_short_cache(db, account, msgs, "123456")
        return FetchServiceResult(
            ok=True,
            messages=msgs,
            message_count=1,
            folder=folder,
            code="123456",
            account_id=account.id,
            email=account.email,
        )

    with patch("app.services.sync_worker.fetch_account", side_effect=_fake_fetch):
        worker = get_sync_worker()
        detail = worker.sync_one_account(account_id, force=True)
        assert detail["ok"] is True
        assert detail["account_id"] == account_id

    db_session.expire_all()
    refreshed = db_session.get(Account, account_id)
    assert refreshed is not None
    assert refreshed.last_sync_at is not None
    assert refreshed.last_sync_error is None


def test_me_sync_endpoint_gone(client: TestClient) -> None:
    """User-scoped sync routes removed in local-first mode."""
    r = client.post("/api/me/sync")
    assert r.status_code in (404, 410)
