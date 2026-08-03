"""Cloud account quota: live-count floor and counter reconcile."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import Account, AccountPool, AccountStatus, DeviceQuotaState, ProviderType
from app.services import license as license_mod


def test_reserve_floors_on_live_count_after_upgrade(db_session, monkeypatch):
    """Counter at 0 with existing accounts must still enforce cap from live count."""
    settings = SimpleNamespace(
        quota_max_cloud_accounts=2,
        license_token_set=set(),
        license_hmac_secret="",
    )
    monkeypatch.setattr(license_mod, "get_settings", lambda: settings)
    did = "vk_quota_upgrade_device_001"

    for i in range(2):
        db_session.add(
            Account(
                email=f"pre{i}@example.com",
                provider=ProviderType.unknown,
                pool=AccountPool.user_private,
                owner_user_id=did,
                status=AccountStatus.ok,
            )
        )
    db_session.commit()
    # No DeviceQuotaState row — simulates upgrade without backfill
    assert db_session.get(DeviceQuotaState, did) is None

    with pytest.raises(ValueError, match="quota exceeded"):
        license_mod.reserve_cloud_account_slot(db_session, did, settings=settings)

    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    assert row.cloud_accounts_used == 2


def test_reserve_uses_max_of_counter_and_live(db_session, monkeypatch):
    settings = SimpleNamespace(
        quota_max_cloud_accounts=3,
        license_token_set=set(),
        license_hmac_secret="",
    )
    monkeypatch.setattr(license_mod, "get_settings", lambda: settings)
    did = "vk_quota_max_device_002"

    db_session.add(
        Account(
            email="one@example.com",
            provider=ProviderType.unknown,
            pool=AccountPool.user_private,
            owner_user_id=did,
            status=AccountStatus.ok,
        )
    )
    db_session.add(DeviceQuotaState(device_id=did, cloud_accounts_used=0))
    db_session.commit()

    license_mod.reserve_cloud_account_slot(db_session, did, settings=settings)
    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    # live=1, counter was 0 → effective 1, then +1 → 2
    assert row.cloud_accounts_used == 2


def test_reconcile_sets_counter_to_live(db_session):
    did = "vk_quota_reconcile_003"
    for i in range(3):
        db_session.add(
            Account(
                email=f"r{i}@example.com",
                provider=ProviderType.unknown,
                pool=AccountPool.user_private,
                owner_user_id=did,
                status=AccountStatus.ok,
            )
        )
    db_session.add(DeviceQuotaState(device_id=did, cloud_accounts_used=99))
    db_session.commit()

    n = license_mod.reconcile_cloud_account_used(db_session, did)
    assert n == 3
    assert db_session.get(DeviceQuotaState, did).cloud_accounts_used == 3


def test_release_after_delete_syncs_live(db_session, monkeypatch):
    settings = SimpleNamespace(
        quota_max_cloud_accounts=10,
        license_token_set=set(),
        license_hmac_secret="",
    )
    monkeypatch.setattr(license_mod, "get_settings", lambda: settings)
    did = "vk_quota_release_004"
    accs = []
    for i in range(2):
        a = Account(
            email=f"del{i}@example.com",
            provider=ProviderType.unknown,
            pool=AccountPool.user_private,
            owner_user_id=did,
            status=AccountStatus.ok,
        )
        db_session.add(a)
        accs.append(a)
    db_session.add(DeviceQuotaState(device_id=did, cloud_accounts_used=2))
    db_session.commit()

    db_session.delete(accs[0])
    db_session.flush()
    license_mod.release_cloud_account_slot(db_session, did)
    db_session.commit()

    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    assert row.cloud_accounts_used == 1


def test_release_does_not_clobber_higher_counter(db_session, monkeypatch):
    """A concurrent create reservation must not be overwritten by a delete release."""
    settings = SimpleNamespace(
        quota_max_cloud_accounts=10,
        license_token_set=set(),
        license_hmac_secret="",
    )
    monkeypatch.setattr(license_mod, "get_settings", lambda: settings)
    did = "vk_quota_release_race_005"

    accs = []
    for i in range(2):
        a = Account(
            email=f"race{i}@example.com",
            provider=ProviderType.unknown,
            pool=AccountPool.user_private,
            owner_user_id=did,
            status=AccountStatus.ok,
        )
        db_session.add(a)
        accs.append(a)
    db_session.add(DeviceQuotaState(device_id=did, cloud_accounts_used=2))
    db_session.commit()

    db_session.delete(accs[0])
    db_session.flush()
    # Simulate a concurrent create that already reserved a slot before release.
    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    row.cloud_accounts_used = 3
    db_session.flush()

    license_mod.release_cloud_account_slot(db_session, did)
    db_session.commit()

    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    assert row.cloud_accounts_used == 2


def test_reserve_conditional_update_at_cap(db_session, monkeypatch):
    """Conditional UPDATE must not overshoot when already at cap."""
    settings = SimpleNamespace(
        quota_max_cloud_accounts=1,
        license_token_set=set(),
        license_hmac_secret="",
    )
    monkeypatch.setattr(license_mod, "get_settings", lambda: settings)
    did = "vk_quota_cond_006"
    db_session.add(
        Account(
            email="only@example.com",
            provider=ProviderType.unknown,
            pool=AccountPool.user_private,
            owner_user_id=did,
            status=AccountStatus.ok,
        )
    )
    db_session.add(DeviceQuotaState(device_id=did, cloud_accounts_used=1))
    db_session.commit()

    with pytest.raises(ValueError, match="quota exceeded"):
        license_mod.reserve_cloud_account_slot(db_session, did, settings=settings)

    row = db_session.get(DeviceQuotaState, did)
    assert row is not None
    assert row.cloud_accounts_used == 1
