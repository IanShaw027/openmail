"""At-rest field encryption helpers (AES-256-GCM).

Sensitive columns (password, refresh_token, cookies, etc.) must be encrypted
with OPENMAIL_MASTER_KEY before persistence.

Decrypt tries the primary key, then OPENMAIL_MASTER_KEY_FALLBACKS (previous
keys after rotation). Encrypt always uses the primary key. Call
``reencrypt_token`` / startup migrate to rewrite rows under the primary key.
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, get_settings

_NONCE_LEN = 12
_TAG_LEN = 16  # AESGCM appends tag to ciphertext

logger = logging.getLogger("openmail.crypto")


class CryptoError(Exception):
    """Raised when encryption/decryption fails or key is invalid."""


def _decode_master_key(raw: str) -> bytes:
    if not raw or not raw.strip():
        raise CryptoError(
            "OPENMAIL_MASTER_KEY is not set. "
            "Generate with: python -c \"import os,base64; "
            "print(base64.b64encode(os.urandom(32)).decode())\""
        )
    key_str = raw.strip().strip('"').strip("'")
    # Prefer base64 (standard or url-safe)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            key = decoder(key_str)
            if len(key) == 32:
                return key
        except Exception:
            pass
    # Hex (64 chars -> 32 bytes)
    try:
        key = bytes.fromhex(key_str)
        if len(key) == 32:
            return key
    except ValueError:
        pass
    raise CryptoError(
        "OPENMAIL_MASTER_KEY must be 32 bytes as base64 or 64 hex characters"
    )


def _split_fallback_keys(raw: str | None) -> list[str]:
    """Parse OPENMAIL_MASTER_KEY_FALLBACKS: comma and/or newline separated."""
    if not raw or not str(raw).strip():
        return []
    text = str(raw).replace("\r\n", "\n").replace(",", "\n")
    out: list[str] = []
    for part in text.split("\n"):
        p = part.strip().strip('"').strip("'")
        if p:
            out.append(p)
    return out


@lru_cache(maxsize=8)
def _aesgcm_for_raw_key(raw_key: str) -> AESGCM:
    return AESGCM(_decode_master_key(raw_key))


def _all_decrypt_keys(settings: Settings) -> list[tuple[str, AESGCM]]:
    """Primary first, then unique fallbacks (decoded)."""
    pairs: list[tuple[str, AESGCM]] = []
    seen: set[bytes] = set()
    primary = (settings.openmail_master_key or "").strip()
    fallbacks = _split_fallback_keys(
        getattr(settings, "openmail_master_key_fallbacks", None)
        or os.environ.get("OPENMAIL_MASTER_KEY_FALLBACKS", "")
    )
    for raw in [primary, *fallbacks]:
        if not raw:
            continue
        try:
            key_bytes = _decode_master_key(raw)
        except CryptoError:
            logger.warning("skip invalid master key candidate")
            continue
        if key_bytes in seen:
            continue
        seen.add(key_bytes)
        pairs.append((raw, _aesgcm_for_raw_key(raw)))
    return pairs


def get_aesgcm(settings: Settings | None = None) -> AESGCM:
    """AESGCM for **encryption** (always primary key)."""
    s = settings or get_settings()
    return AESGCM(_decode_master_key(s.openmail_master_key))


def encrypt_bytes(plaintext: bytes, *, settings: Settings | None = None) -> bytes:
    """Return nonce || ciphertext+tag (binary). Always primary key."""
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    aesgcm = get_aesgcm(settings)
    nonce = os.urandom(_NONCE_LEN)
    ct = aesgcm.encrypt(nonce, bytes(plaintext), None)
    return nonce + ct


def decrypt_bytes(blob: bytes, *, settings: Settings | None = None) -> bytes:
    """Decrypt with primary key, then fallbacks."""
    if not blob or len(blob) < _NONCE_LEN + _TAG_LEN:
        raise CryptoError("ciphertext blob too short")
    s = settings or get_settings()
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    last_exc: Exception | None = None
    for _raw, aesgcm in _all_decrypt_keys(s):
        try:
            return aesgcm.decrypt(nonce, ct, None)
        except Exception as exc:
            last_exc = exc
            continue
    raise CryptoError("decryption failed") from last_exc


def encrypt_str(plaintext: str, *, settings: Settings | None = None) -> str:
    """Encrypt UTF-8 string; return url-safe base64 of nonce||ct."""
    raw = encrypt_bytes(plaintext.encode("utf-8"), settings=settings)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decrypt_str(token: str, *, settings: Settings | None = None) -> str:
    try:
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception as exc:
        raise CryptoError("invalid ciphertext encoding") from exc
    return decrypt_bytes(blob, settings=settings).decode("utf-8")


def encrypt_json(value: Any, *, settings: Settings | None = None) -> str:
    import json

    return encrypt_str(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        settings=settings,
    )


def decrypt_json(token: str, *, settings: Settings | None = None) -> Any:
    import json

    return json.loads(decrypt_str(token, settings=settings))


def reencrypt_token(token: str | None, *, settings: Settings | None = None) -> str | None:
    """Decrypt (primary or fallback) and re-encrypt with primary. None if empty.

    Returns original token if already under primary (decrypt+encrypt may change
    ciphertext due to new nonce — still valid). On decrypt failure returns None
    without raising (caller decides).
    """
    if not token:
        return None
    try:
        plain = decrypt_str(token, settings=settings)
    except CryptoError:
        return None
    return encrypt_str(plain, settings=settings)


def master_key_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if not s.openmail_master_key or not s.openmail_master_key.strip():
        return False
    try:
        _decode_master_key(s.openmail_master_key)
        return True
    except CryptoError:
        return False


def clear_key_cache() -> None:
    """Drop cached AESGCM instances (tests / after settings reload)."""
    _aesgcm_for_raw_key.cache_clear()
