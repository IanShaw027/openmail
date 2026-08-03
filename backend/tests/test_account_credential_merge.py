"""Account PATCH credential deep-merge (partial keys must not wipe blob)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.crypto import encrypt_json
from app.models import Account, AccountPool, AccountSession, AccountStatus, ProviderType
from app.routers.accounts import _apply_update
from app.schemas import AccountUpdate
from app.services.credentials import load_credentials


def test_apply_update_credential_deep_merges(db_session):
    settings = get_settings()
    acc = Account(
        email="merge@example.com",
        provider=ProviderType.oauth,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_merge_001",
        status=AccountStatus.ok,
        credential_enc=encrypt_json(
            {
                "client_id": "11111111-1111-1111-1111-111111111111",
                "refresh_token": "M." + "x" * 200,
                "tenant": "common",
            },
            settings=settings,
        ),
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)

    _apply_update(
        acc,
        AccountUpdate(credential={"access_token": "at_new"}),
        db=db_session,
        settings=settings,
    )

    db_session.refresh(acc)
    creds = load_credentials(acc, settings=settings)
    assert creds.get("client_id") == "11111111-1111-1111-1111-111111111111"
    assert creds.get("refresh_token", "").startswith("M.")
    assert creds.get("tenant") == "common"
    assert creds.get("access_token") == "at_new"


def test_apply_update_credential_empty_string_clears_key(db_session):
    settings = get_settings()
    acc = Account(
        email="clear@example.com",
        provider=ProviderType.oauth,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_clear_001",
        status=AccountStatus.ok,
        credential_enc=encrypt_json(
            {
                "client_id": "22222222-2222-2222-2222-222222222222",
                "refresh_token": "M." + "y" * 200,
                "access_token": "old_at",
            },
            settings=settings,
        ),
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)

    _apply_update(
        acc,
        AccountUpdate(credential={"access_token": ""}),
        db=db_session,
        settings=settings,
    )

    db_session.refresh(acc)
    creds = load_credentials(acc, settings=settings)
    assert "access_token" not in creds
    assert creds.get("client_id") == "22222222-2222-2222-2222-222222222222"
    assert creds.get("refresh_token", "").startswith("M.")


def test_apply_update_credential_empty_dict_preserves_existing(db_session):
    """Incoming empty dict after merge keeps prior keys (no wipe)."""
    settings = get_settings()
    acc = Account(
        email="keep@example.com",
        provider=ProviderType.oauth,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_keep_001",
        status=AccountStatus.ok,
        credential_enc=encrypt_json(
            {"client_id": "33333333-3333-3333-3333-333333333333", "api_url": "https://x.test"},
            settings=settings,
        ),
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)

    _apply_update(
        acc,
        AccountUpdate(credential={}),
        db=db_session,
        settings=settings,
    )

    db_session.refresh(acc)
    creds = load_credentials(acc, settings=settings)
    assert creds.get("client_id") == "33333333-3333-3333-3333-333333333333"
    assert creds.get("api_url") == "https://x.test"


def test_apply_update_client_sealed_clears_server_session(db_session):
    settings = get_settings()
    acc = Account(
        email="sealed@example.com",
        provider=ProviderType.oauth,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_sealed_001",
        status=AccountStatus.ok,
        credential_enc=encrypt_json({"client_id": "44444444-4444-4444-4444-444444444444"}, settings=settings),
    )
    db_session.add(acc)
    db_session.flush()
    db_session.add(
        AccountSession(
            account_id=acc.id,
            cookies_enc=encrypt_json([{"name": "sid", "value": "abc"}], settings=settings),
            valid=True,
        )
    )
    db_session.commit()
    db_session.refresh(acc)
    assert acc.session is not None

    _apply_update(
        acc,
        AccountUpdate(client_sealed="{\"sealed\":true}"),
        db=db_session,
        settings=settings,
    )

    db_session.refresh(acc)
    assert acc.session is None
    assert db_session.query(AccountSession).filter(AccountSession.account_id == acc.id).count() == 0


def test_apply_update_rejects_credential_patch_on_sealed_without_flag(db_session):
    from fastapi import HTTPException

    from app.services.credentials import CLIENT_SEALED_KEY, save_client_sealed

    settings = get_settings()
    acc = Account(
        email="sealed-patch@example.com",
        provider=ProviderType.oauth,
        pool=AccountPool.user_private,
        owner_user_id="vk_test_device_sealed_patch",
        status=AccountStatus.ok,
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)
    save_client_sealed(acc, '{"sealed":true}', settings=settings)
    db_session.commit()
    db_session.refresh(acc)

    with pytest.raises(HTTPException) as ei:
        _apply_update(
            acc,
            AccountUpdate(credential={"access_token": "sneak"}),
            db=db_session,
            settings=settings,
        )
    assert ei.value.status_code == 409

    _apply_update(
        acc,
        AccountUpdate(
            credential={"_om_unwrap_sealed": True, "access_token": "ok"}
        ),
        db=db_session,
        settings=settings,
    )
    db_session.refresh(acc)
    creds = load_credentials(acc, settings=settings)
    assert not creds.get(CLIENT_SEALED_KEY)
    assert creds.get("access_token") == "ok"
