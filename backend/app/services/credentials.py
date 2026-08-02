"""Load / save decrypted credentials and session cookies for Account rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import (
    CryptoError,
    decrypt_json,
    decrypt_str,
    encrypt_json,
    encrypt_str,
)
from app.models import Account, AccountSession
from app.providers.base import CredentialUpdates

import re

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def looks_like_microsoft_client_id(value: str) -> bool:
    return bool(_UUID_RE.match(str(value or "").strip()))


def looks_like_microsoft_refresh_token(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith("M.") or text.startswith("m.") or len(text) > 180


def normalize_oauth_credential_fields(creds: dict[str, Any]) -> dict[str, Any]:
    """Auto-swap client_id / refresh_token when pasted in either order."""
    out = dict(creds or {})
    cid = str(out.get("client_id") or "").strip()
    rt = str(out.get("refresh_token") or "").strip()
    if not cid and not rt:
        return out
    if looks_like_microsoft_client_id(cid) and looks_like_microsoft_refresh_token(rt):
        out["client_id"] = cid
        out["refresh_token"] = rt
        return out
    if looks_like_microsoft_client_id(rt) and looks_like_microsoft_refresh_token(cid):
        out["client_id"] = rt
        out["refresh_token"] = cid
        return out
    if looks_like_microsoft_client_id(rt) and not looks_like_microsoft_client_id(cid):
        out["client_id"] = rt
        if looks_like_microsoft_refresh_token(cid):
            out["refresh_token"] = cid
    elif looks_like_microsoft_refresh_token(cid) and not looks_like_microsoft_refresh_token(rt):
        out["refresh_token"] = cid
        if looks_like_microsoft_client_id(rt):
            out["client_id"] = rt
    return out


CLIENT_SEALED_KEY = "_om_client_sealed"


def is_client_sealed_blob(data: dict[str, Any] | None) -> bool:
    return bool(data and data.get(CLIENT_SEALED_KEY) is True and data.get("blob"))


def load_credentials(account: Account, *, settings: Settings | None = None) -> dict[str, Any]:
    """Decrypt credential_enc JSON; empty dict if missing/invalid.

    If payload is client-sealed, returns {CLIENT_SEALED_KEY: True, blob: ...}
    without server ability to recover plaintext secrets.
    """
    s = settings or get_settings()
    if not account.credential_enc:
        return {}
    try:
        data = decrypt_json(account.credential_enc, settings=s)
        if isinstance(data, dict):
            if is_client_sealed_blob(data):
                return data
            return normalize_oauth_credential_fields(data)
        return {}
    except (CryptoError, TypeError, ValueError):
        return {}


def save_client_sealed(
    account: Account,
    sealed_blob: str,
    *,
    settings: Settings | None = None,
) -> None:
    """Store vault-sealed credential blob. Server master key only wraps outer JSON."""
    s = settings or get_settings()
    payload = {CLIENT_SEALED_KEY: True, "blob": sealed_blob, "v": 1}
    account.credential_enc = encrypt_json(payload, settings=s)
    # Clear server-side password when moving to client seal
    account.password_enc = None
    account.updated_at = datetime.now(timezone.utc)


def load_password(account: Account, *, settings: Settings | None = None) -> str | None:
    s = settings or get_settings()
    if not account.password_enc:
        return None
    try:
        return decrypt_str(account.password_enc, settings=s)
    except CryptoError:
        return None


def load_cookies(account: Account, *, settings: Settings | None = None) -> list[dict[str, Any]] | None:
    s = settings or get_settings()
    sess = account.session
    if sess is None or not sess.cookies_enc:
        return None
    try:
        data = decrypt_json(sess.cookies_enc, settings=s)
        if isinstance(data, list):
            return data
        return None
    except (CryptoError, TypeError, ValueError):
        return None


def save_credentials(
    account: Account,
    credentials: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> None:
    s = settings or get_settings()
    account.credential_enc = encrypt_json(credentials, settings=s)
    account.updated_at = datetime.now(timezone.utc)


def apply_credential_updates(
    db: Session,
    account: Account,
    updates: CredentialUpdates | None,
    *,
    settings: Settings | None = None,
    base_credentials: dict[str, Any] | None = None,
) -> None:
    """Merge CredentialUpdates into encrypted storage (in-place on account)."""
    if updates is None or not updates.any():
        return
    s = settings or get_settings()
    creds = dict(base_credentials) if base_credentials is not None else load_credentials(account, settings=s)

    if updates.refresh_token:
        creds["refresh_token"] = updates.refresh_token
    if updates.access_token:
        creds["access_token"] = updates.access_token
    if updates.password:
        account.password_enc = encrypt_str(updates.password, settings=s)

    if updates.refresh_token or updates.access_token:
        save_credentials(account, creds, settings=s)

    if updates.session_cookies is not None or updates.session_meta is not None:
        if account.session is None:
            account.session = AccountSession(account_id=account.id)
            db.add(account.session)
        if updates.session_cookies is not None:
            account.session.cookies_enc = encrypt_json(updates.session_cookies, settings=s)
        if updates.session_meta is not None:
            account.session.meta_enc = encrypt_json(updates.session_meta, settings=s)
        account.session.saved_at = datetime.now(timezone.utc)
        account.session.valid = True
        account.session.last_validated_at = datetime.now(timezone.utc)

    account.updated_at = datetime.now(timezone.utc)


def merge_guest_credentials(
    *,
    password: str | None = None,
    credential: dict[str, Any] | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a credentials dict for guest/proxy fetch (not persisted)."""
    creds: dict[str, Any] = normalize_oauth_credential_fields(dict(credential or {}))
    if password:
        creds["password"] = password
    if cookies is not None:
        creds["cookies"] = cookies
    return creds
