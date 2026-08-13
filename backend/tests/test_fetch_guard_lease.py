"""Fetch lock lease expiry + code-API cache TTL behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.fetch_guard import (
    FetchInFlightError,
    _acquire_lease,
    _lease_active,
    account_fetch_slot,
    lease_is_current,
)
from app.models import FetchLockState


def test_lease_active_expires():
    state = FetchLockState(account_id="acc_1", in_flight=True)
    state.updated_at = datetime.now(timezone.utc) - timedelta(seconds=200)
    assert _lease_active(state, lease_seconds=180) is False


def test_lease_active_within_window():
    state = FetchLockState(account_id="acc_1", in_flight=True)
    state.updated_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _lease_active(state, lease_seconds=180) is True


def test_lease_inactive_when_not_in_flight():
    state = FetchLockState(account_id="acc_1", in_flight=False)
    state.updated_at = datetime.now(timezone.utc)
    assert _lease_active(state, lease_seconds=180) is False


def test_stale_in_flight_allows_new_slot(db_session):
    """Crash leftover: in_flight True but updated_at old → new fetch may proceed."""
    acc_id = "acc_lease_test"
    st = FetchLockState(account_id=acc_id, in_flight=True)
    st.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add(st)
    db_session.commit()

    settings = SimpleNamespace(
        fetch_lock_lease_seconds=60.0,
        fetch_min_interval_seconds=0.0,
    )

    with account_fetch_slot(db_session, acc_id, settings=settings, force=True):
        pass

    st2 = db_session.get(FetchLockState, acc_id)
    assert st2 is not None
    assert st2.in_flight is False


def test_fresh_in_flight_blocks(db_session):
    acc_id = "acc_lease_block"
    st = FetchLockState(account_id=acc_id, in_flight=True)
    st.updated_at = datetime.now(timezone.utc)
    db_session.add(st)
    db_session.commit()

    settings = SimpleNamespace(
        fetch_lock_lease_seconds=180.0,
        fetch_min_interval_seconds=0.0,
    )

    with pytest.raises(FetchInFlightError):
        with account_fetch_slot(db_session, acc_id, settings=settings, force=True):
            pass


def test_old_owner_cannot_release_replacement_lease(db_session):
    """A timed-out request must not clear a lease acquired by another worker."""
    from sqlalchemy import update

    acc_id = "acc_lease_owner"
    token, _ = _acquire_lease(db_session, acc_id, lease_seconds=60.0)
    replacement = "replacement-token"
    db_session.execute(
        update(FetchLockState)
        .where(FetchLockState.account_id == acc_id)
        .values(lease_token=replacement, in_flight=True)
    )
    db_session.commit()

    released = db_session.execute(
        update(FetchLockState)
        .where(
            FetchLockState.account_id == acc_id,
            FetchLockState.lease_token == token,
        )
        .values(in_flight=False, lease_token=None)
    )
    db_session.commit()

    state = db_session.get(FetchLockState, acc_id)
    assert released.rowcount == 0
    assert state is not None
    assert state.in_flight is True
    assert state.lease_token == replacement


def test_replacement_lease_invalidates_previous_owner(db_session):
    acc_id = "acc_lease_validity"
    token, _ = _acquire_lease(db_session, acc_id, lease_seconds=60.0)
    assert lease_is_current(db_session, acc_id, token) is True

    from sqlalchemy import update

    db_session.execute(
        update(FetchLockState)
        .where(FetchLockState.account_id == acc_id)
        .values(lease_token="replacement-token", in_flight=True)
    )
    db_session.commit()

    assert lease_is_current(db_session, acc_id, token) is False


def _code_account(**kwargs):
    from app.models import AccountStatus, ProviderType

    now = datetime.now(timezone.utc)
    base = dict(
        id="acc_c",
        email="a@b.com",
        status=AccountStatus.ok,
        latest_verification_code="123456",
        latest_code_at=now - timedelta(seconds=10),
        latest_code_folder="inbox",
        provider=ProviderType.imap,
        password_enc=None,
        credential_enc=None,
        session=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_code_cache_ttl_skips_stale():
    """When latest_code_at is older than TTL, must not short-circuit as cached."""
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock, patch

    now = datetime.now(timezone.utc)
    account = _code_account(
        latest_verification_code="123456",
        latest_code_at=now - timedelta(seconds=500),
    )
    settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=0.0,
        fetch_lock_lease_seconds=180.0,
    )
    db = MagicMock()
    # After cache miss, credential build may run — stub to a clean failure
    with patch(
        "app.services.fetch_service._build_credentials_for_account",
        side_effect=Exception("no creds"),
    ):
        try:
            result = fetch_account(
                db,
                account,  # type: ignore[arg-type]
                use_cache=True,
                force=False,
                settings=settings,  # type: ignore[arg-type]
            )
        except Exception:
            # Uncaught is fine — cache path did not return early with cached=True
            return
    assert not (result.ok and result.cached and result.code == "123456")


def test_code_cache_ttl_hits_fresh():
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock

    now = datetime.now(timezone.utc)
    account = _code_account(
        latest_verification_code="654321",
        latest_code_at=now - timedelta(seconds=10),
    )
    settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=0.0,
        fetch_lock_lease_seconds=180.0,
    )
    db = MagicMock()
    result = fetch_account(
        db,
        account,  # type: ignore[arg-type]
        use_cache=True,
        force=False,
        settings=settings,  # type: ignore[arg-type]
    )
    assert result.ok is True
    assert result.cached is True
    assert result.code == "654321"


def test_code_cache_returns_plaintext_from_encrypted_latest_code():
    from app.config import get_settings
    from app.crypto import encrypt_str
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock

    now = datetime.now(timezone.utc)
    settings = get_settings()
    account = _code_account(
        latest_verification_code=encrypt_str("654321", settings=settings),
        latest_code_at=now - timedelta(seconds=10),
    )
    cache_settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=0.0,
        fetch_lock_lease_seconds=180.0,
        openmail_master_key=settings.openmail_master_key,
        openmail_master_key_fallbacks=getattr(settings, "openmail_master_key_fallbacks", "") or "",
    )
    result = fetch_account(
        MagicMock(),
        account,  # type: ignore[arg-type]
        use_cache=True,
        force=False,
        settings=cache_settings,  # type: ignore[arg-type]
    )
    assert result.ok is True
    assert result.cached is True
    assert result.code == "654321"


def test_write_short_cache_encrypts_latest_code(db_session):
    from app.crypto import decrypt_str
    from app.models import Account, AccountPool, AccountStatus, ProviderType
    from app.services.fetch_service import _write_short_cache

    acc = Account(
        email="otp@example.com",
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
        owner_user_id="vk_otp_enc",
        status=AccountStatus.ok,
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)
    _write_short_cache(db_session, acc, [], "123456", folder="inbox")
    db_session.commit()
    assert acc.latest_verification_code
    assert acc.latest_verification_code != "123456"
    assert decrypt_str(acc.latest_verification_code) == "123456"


def test_code_cache_folder_mismatch_does_not_hit():
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock, patch

    account = _code_account(latest_code_folder="spam")
    settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=0.0,
        fetch_lock_lease_seconds=180.0,
    )
    with patch(
        "app.services.fetch_service._build_credentials_for_account",
        side_effect=Exception("no creds"),
    ):
        with pytest.raises(Exception, match="no creds"):
            fetch_account(
                MagicMock(), account, use_cache=True, settings=settings  # type: ignore[arg-type]
            )


def test_code_cache_missing_timestamp_not_cached():
    """latest_code_at None must not serve forever as cached."""
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock, patch

    account = _code_account(
        latest_verification_code="999999",
        latest_code_at=None,
    )
    settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=0.0,
        fetch_lock_lease_seconds=180.0,
    )
    db = MagicMock()
    with patch(
        "app.services.fetch_service._build_credentials_for_account",
        side_effect=Exception("no creds"),
    ):
        try:
            result = fetch_account(
                db,
                account,  # type: ignore[arg-type]
                use_cache=True,
                force=False,
                settings=settings,  # type: ignore[arg-type]
            )
        except Exception:
            return
    assert not (result.ok and result.cached)


@pytest.mark.parametrize("guard_error", ["too_soon", "in_flight"])
def test_guard_fallback_rejects_stale_cache(guard_error):
    from app.fetch_guard import FetchTooSoonError
    from app.services.fetch_service import fetch_account
    from unittest.mock import MagicMock, patch

    account = _code_account(latest_code_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    settings = SimpleNamespace(
        code_api_cache_ttl_seconds=90.0,
        fetch_min_interval_seconds=10.0,
        fetch_lock_lease_seconds=180.0,
    )
    exc = (
        FetchTooSoonError(5.0)
        if guard_error == "too_soon"
        else FetchInFlightError(retry_after=7.0)
    )
    with (
        patch("app.services.fetch_service._build_credentials_for_account", return_value={}),
        patch("app.services.fetch_service.resolve_provider", return_value=MagicMock()),
        patch("app.services.fetch_service.account_fetch_slot", side_effect=exc),
    ):
        result = fetch_account(
            MagicMock(), account, use_cache=False, settings=settings  # type: ignore[arg-type]
        )
    assert result.ok is False
    assert result.cached is False
    assert result.code is None
    assert result.retry_after is not None
    assert result.retry_after == (5.0 if guard_error == "too_soon" else 7.0)
    if guard_error == "in_flight":
        assert "7" in (result.error or "")


def test_sync_cycle_flag_resets_after_crash(monkeypatch):
    from app.services.sync_worker import SyncWorker

    worker = SyncWorker()

    def crash(*, trigger):
        raise RuntimeError(trigger)

    monkeypatch.setattr(worker, "_run_cycle_body", crash)
    with pytest.raises(RuntimeError, match="test"):
        worker._run_cycle(trigger="test")
    assert worker._running_cycle is False


def test_cloud_account_owner_email_is_unique(db_session):
    from app.models import Account, AccountPool, ProviderType
    from sqlalchemy.exc import IntegrityError

    first = Account(
        email="same@example.com",
        owner_user_id="vk_owner",
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
    )
    db_session.add(first)
    db_session.commit()
    db_session.add(
        Account(
            email="same@example.com",
            owner_user_id="vk_owner",
            provider=ProviderType.imap,
            pool=AccountPool.user_private,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
