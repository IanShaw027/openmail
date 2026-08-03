"""Durable device poll quota (device_poll_events table)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    Account,
    AccountPool,
    AccountStatus,
    DevicePollEvent,
    DevicePollQuotaState,
    DeviceQuotaState,
    ProviderType,
)
from app.services import license as license_mod


@pytest.fixture(autouse=True)
def _clear_snap_cache():
    with license_mod._snap_lock:
        license_mod._snap_cache.clear()
    yield
    with license_mod._snap_lock:
        license_mod._snap_cache.clear()


def test_check_poll_quota_persists_and_limits(db_session, monkeypatch):
    class S:
        quota_max_poll_per_hour = 3
        license_token_set = set()
        license_hmac_secret = ""

    monkeypatch.setattr(license_mod, "get_settings", lambda: S())
    did = "vk_test_device_quota_abc"

    for i in range(3):
        ok, err = license_mod.check_poll_quota(did, db=db_session)
        assert ok is True, err

    ok, err = license_mod.check_poll_quota(did, db=db_session)
    assert ok is False
    assert err and "quota exceeded" in err

    n = db_session.query(DevicePollEvent).filter(DevicePollEvent.device_id == did).count()
    assert n == 3


def test_check_poll_quota_uses_poll_lock_table(db_session, monkeypatch):
    class S:
        quota_max_poll_per_hour = 1
        license_token_set = set()
        license_hmac_secret = ""

    monkeypatch.setattr(license_mod, "get_settings", lambda: S())
    did = "vk_poll_lock_device"

    ok, err = license_mod.check_poll_quota(did, db=db_session)
    assert ok is True, err

    assert db_session.get(DevicePollQuotaState, did) is not None
    assert db_session.get(DeviceQuotaState, did) is None


def test_licensed_skips_quota(db_session, monkeypatch):
    class S:
        quota_max_poll_per_hour = 1
        license_token_set = {"unlimited-token"}
        license_hmac_secret = ""

    monkeypatch.setattr(license_mod, "get_settings", lambda: S())
    for _ in range(5):
        ok, err = license_mod.check_poll_quota(
            "vk_any",
            license_token="unlimited-token",
            db=db_session,
        )
        assert ok is True, err

    n = db_session.query(DevicePollEvent).count()
    assert n == 0


def test_poll_used_in_hour_counts_window(db_session, monkeypatch):
    class S:
        quota_max_poll_per_hour = 100
        license_token_set = set()
        license_hmac_secret = ""

    monkeypatch.setattr(license_mod, "get_settings", lambda: S())
    did = "vk_window_device"
    now = datetime.now(timezone.utc)
    # old event outside window
    db_session.add(
        DevicePollEvent(device_id=did, ts=now - timedelta(hours=2))
    )
    db_session.add(DevicePollEvent(device_id=did, ts=now - timedelta(minutes=10)))
    db_session.add(DevicePollEvent(device_id=did, ts=now - timedelta(minutes=5)))
    db_session.commit()

    used = license_mod.poll_used_in_hour(did, db=db_session)
    assert used == 2


def test_cloud_quota_backfills_existing_accounts(db_session, monkeypatch):
    class S:
        quota_max_poll_per_hour = 100
        quota_max_cloud_accounts = 3
        license_token_set = set()
        license_hmac_secret = ""

    monkeypatch.setattr(license_mod, "get_settings", lambda: S())
    did = "vk_backfill_device"
    db_session.add_all(
        [
            Account(
                email="one@example.com",
                owner_user_id=did,
                pool=AccountPool.user_private,
                provider=ProviderType.oauth,
                status=AccountStatus.ok,
            ),
            Account(
                email="two@example.com",
                owner_user_id=did,
                pool=AccountPool.user_private,
                provider=ProviderType.oauth,
                status=AccountStatus.ok,
            ),
        ]
    )
    db_session.commit()

    license_mod.reserve_cloud_account_slot(db_session, did, settings=S())
    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    assert row.cloud_accounts_used == 3

    with pytest.raises(ValueError):
        license_mod.reserve_cloud_account_slot(db_session, did, settings=S())
