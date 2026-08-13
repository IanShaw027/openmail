"""Master-key rotation must rewrite device_registry.json; decrypt skip is fail-closed."""

from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest

from app.config import Settings, get_settings
from app.crypto import clear_key_cache, decrypt_str


def _b64_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _pair():
    secret = os.urandom(32)
    b64 = base64.urlsafe_b64encode(secret).decode().rstrip("=")
    pid = "vk_" + hashlib.sha256(secret).hexdigest()[:40]
    return secret, b64, pid


@pytest.fixture(autouse=True)
def _clear_aes_cache():
    clear_key_cache()
    yield
    clear_key_cache()
    get_settings.cache_clear()


def _reload_da(tmp_path, monkeypatch, *, primary: str, fallbacks: str = ""):
    monkeypatch.setenv("OPENMAIL_MASTER_KEY", primary)
    monkeypatch.setenv("OPENMAIL_MASTER_KEY_FALLBACKS", fallbacks)
    monkeypatch.setenv("OPENMAIL_DEVICE_REGISTRY_PATH", str(tmp_path / "reg.json"))
    get_settings.cache_clear()
    clear_key_cache()
    import app.services.device_auth as module
    from importlib import reload

    module = reload(module)
    module._loaded = False
    module._secrets.clear()
    module._registry.clear()
    module._status.clear()
    module._created_at.clear()
    return module


def test_rewrite_registry_moves_secrets_to_primary(tmp_path, monkeypatch):
    old_key = _b64_key()
    new_key = _b64_key()
    da = _reload_da(tmp_path, monkeypatch, primary=old_key)
    _, b64, pid = _pair()
    da.register_device_secret(pid, b64)
    path = tmp_path / "reg.json"
    old_blob = json.loads(path.read_text(encoding="utf-8"))
    old_enc = old_blob["entries"][0]["secret_enc"]

    da = _reload_da(tmp_path, monkeypatch, primary=new_key, fallbacks=old_key)
    n = da.rewrite_registry_with_primary_key()
    assert n == 1

    new_blob = json.loads(path.read_text(encoding="utf-8"))
    new_enc = new_blob["entries"][0]["secret_enc"]
    assert new_enc != old_enc

    rotated = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks="",
    )
    assert decrypt_str(new_enc, settings=rotated) == b64

    old_only = Settings.model_construct(
        openmail_master_key=old_key,
        openmail_master_key_fallbacks="",
    )
    with pytest.raises(Exception):
        decrypt_str(new_enc, settings=old_only)


def test_load_registry_fail_closed_does_not_wipe_file(tmp_path, monkeypatch):
    old_key = _b64_key()
    new_key = _b64_key()
    da = _reload_da(tmp_path, monkeypatch, primary=old_key)
    _, b64, pid = _pair()
    da.register_device_secret(pid, b64)
    path = tmp_path / "reg.json"
    before = path.read_text(encoding="utf-8")

    da = _reload_da(tmp_path, monkeypatch, primary=new_key, fallbacks="")
    with pytest.raises(RuntimeError):
        da.load_registry()

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["entries"][0]["public_id"] == pid


def test_migrate_reencrypt_all_rewrites_registry(tmp_path, monkeypatch):
    from app.services.crypto_migrate import migrate_reencrypt_all
    from sqlalchemy.orm import Session

    old_key = _b64_key()
    new_key = _b64_key()
    da = _reload_da(tmp_path, monkeypatch, primary=old_key)
    _, b64, pid = _pair()
    da.register_device_secret(pid, b64)

    da = _reload_da(tmp_path, monkeypatch, primary=new_key, fallbacks=old_key)
    settings = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks=old_key,
    )
    totals = migrate_reencrypt_all(_FakeDb(), settings=settings)
    assert totals.get("registry", 0) == 1

    new_enc = json.loads((tmp_path / "reg.json").read_text(encoding="utf-8"))["entries"][0][
        "secret_enc"
    ]
    primary_only = Settings.model_construct(
        openmail_master_key=new_key,
        openmail_master_key_fallbacks="",
    )
    assert decrypt_str(new_enc, settings=primary_only) == b64


def test_migrate_encrypts_plaintext_otp_without_fallbacks(db_session, monkeypatch):
    """Leftover plaintext OTP must be sealed even when no key rotation is configured."""
    from app.crypto import decrypt_str
    from app.models import Account, AccountPool, AccountStatus, MailItem, ProviderType
    from app.services.crypto_migrate import migrate_reencrypt_all

    monkeypatch.setenv("OPENMAIL_MASTER_KEY_FALLBACKS", "")
    get_settings.cache_clear()
    acc = Account(
        email="otp-plain@example.com",
        provider=ProviderType.imap,
        pool=AccountPool.user_private,
        owner_user_id="vk_otp_plain",
        status=AccountStatus.ok,
        latest_verification_code="123456",
    )
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)
    item = MailItem(
        account_id=acc.id,
        folder="inbox",
        stable_id="p:otp-plain",
        subject="code",
        from_addr="a@b.com",
        preview="your code is 654321",
        verification_code="654321",
    )
    db_session.add(item)
    db_session.commit()

    totals = migrate_reencrypt_all(db_session)
    db_session.refresh(acc)
    db_session.refresh(item)
    assert totals.get("otp_accounts", 0) >= 1
    assert totals.get("otp_mail_items", 0) >= 1
    assert acc.latest_verification_code != "123456"
    assert decrypt_str(acc.latest_verification_code) == "123456"
    assert item.verification_code != "654321"
    assert decrypt_str(item.verification_code) == "654321"
    assert item.preview != "your code is 654321"
    assert decrypt_str(item.preview) == "your code is 654321"


class _FakeDb:
    """migrate_reencrypt_all walks Account rows; empty query is enough here."""

    def query(self, _model):  # type: ignore[no-untyped-def]
        return self

    def all(self):
        return []

    def commit(self):
        return None
