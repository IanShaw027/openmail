"""Master key rotation: decrypt with fallbacks; encrypt with primary."""

from __future__ import annotations

import base64
import os

import pytest

from app.config import Settings
from app.crypto import (
    CryptoError,
    clear_key_cache,
    decrypt_str,
    decrypt_str_or_plain,
    encrypt_str,
    reencrypt_token,
)


def _b64_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _clear_aes_cache():
    clear_key_cache()
    yield
    clear_key_cache()


def test_decrypt_with_fallback_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMAIL_MASTER_KEY", raising=False)
    monkeypatch.delenv("OPENMAIL_MASTER_KEY_FALLBACKS", raising=False)
    old_key = _b64_key()
    new_key = _b64_key()
    old_settings = Settings(
        openmail_master_key=old_key,
        openmail_master_key_fallbacks="",
        _env_file=None,  # type: ignore[call-arg]
    )
    # pydantic-settings: construct without env file
    old_settings = Settings.model_construct(
        openmail_master_key=old_key,
        openmail_master_key_fallbacks="",
    )
    token = encrypt_str("secret-password", settings=old_settings)

    # New primary alone fails
    new_only = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks="",
    )
    with pytest.raises(CryptoError):
        decrypt_str(token, settings=new_only)

    # Primary + old as fallback succeeds
    rotated = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks=old_key,
    )
    assert decrypt_str(token, settings=rotated) == "secret-password"


def test_reencrypt_moves_to_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMAIL_MASTER_KEY", raising=False)
    monkeypatch.delenv("OPENMAIL_MASTER_KEY_FALLBACKS", raising=False)
    old_key = _b64_key()
    new_key = _b64_key()
    old_settings = Settings.model_construct(
        openmail_master_key=old_key,
        openmail_master_key_fallbacks="",
    )
    token = encrypt_str("hello", settings=old_settings)

    rotated = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks=old_key,
    )
    new_token = reencrypt_token(token, settings=rotated)
    assert new_token
    # Decryptable with primary only
    primary_only = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks="",
    )
    assert decrypt_str(new_token, settings=primary_only) == "hello"


def test_encrypt_always_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMAIL_MASTER_KEY", raising=False)
    monkeypatch.delenv("OPENMAIL_MASTER_KEY_FALLBACKS", raising=False)
    primary = _b64_key()
    fallback = _b64_key()
    s = Settings.model_construct(
        openmail_master_key=primary,
        openmail_master_key_fallbacks=fallback,
    )
    token = encrypt_str("x", settings=s)
    # Must open with primary alone
    assert (
        decrypt_str(
            token,
            settings=Settings.model_construct(
                openmail_master_key=primary,
                openmail_master_key_fallbacks="",
            ),
        )
        == "x"
    )


def test_decrypt_str_or_plain_accepts_legacy_plaintext() -> None:
    key = _b64_key()
    s = Settings.model_construct(openmail_master_key=key, openmail_master_key_fallbacks="")
    assert decrypt_str_or_plain("112233", settings=s) == "112233"
    assert decrypt_str_or_plain(None, settings=s) is None
    token = encrypt_str("888777", settings=s)
    assert decrypt_str_or_plain(token, settings=s) == "888777"
