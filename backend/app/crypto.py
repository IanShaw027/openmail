"""At-rest field encryption helpers (AES-256-GCM).

Sensitive columns (password, refresh_token, cookies, etc.) must be encrypted
with OPENMAIL_MASTER_KEY before persistence.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, get_settings

_NONCE_LEN = 12
_TAG_LEN = 16  # AESGCM appends tag to ciphertext


class CryptoError(Exception):
    """Raised when encryption/decryption fails or key is invalid."""


def _decode_master_key(raw: str) -> bytes:
    if not raw or not raw.strip():
        raise CryptoError(
            "OPENMAIL_MASTER_KEY is not set. "
            "Generate with: python -c \"import os,base64; "
            "print(base64.b64encode(os.urandom(32)).decode())\""
        )
    key_str = raw.strip()
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


def get_aesgcm(settings: Settings | None = None) -> AESGCM:
    s = settings or get_settings()
    return AESGCM(_decode_master_key(s.openmail_master_key))


def encrypt_bytes(plaintext: bytes, *, settings: Settings | None = None) -> bytes:
    """Return nonce || ciphertext+tag (binary)."""
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    aesgcm = get_aesgcm(settings)
    nonce = os.urandom(_NONCE_LEN)
    ct = aesgcm.encrypt(nonce, bytes(plaintext), None)
    return nonce + ct


def decrypt_bytes(blob: bytes, *, settings: Settings | None = None) -> bytes:
    if not blob or len(blob) < _NONCE_LEN + _TAG_LEN:
        raise CryptoError("ciphertext blob too short")
    aesgcm = get_aesgcm(settings)
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        return aesgcm.decrypt(nonce, ct, None)
    except Exception as exc:
        raise CryptoError("decryption failed") from exc


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

    return encrypt_str(json.dumps(value, ensure_ascii=False, separators=(",", ":")), settings=settings)


def decrypt_json(token: str, *, settings: Settings | None = None) -> Any:
    import json

    return json.loads(decrypt_str(token, settings=settings))


def master_key_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if not s.openmail_master_key or not s.openmail_master_key.strip():
        return False
    try:
        _decode_master_key(s.openmail_master_key)
        return True
    except CryptoError:
        return False
