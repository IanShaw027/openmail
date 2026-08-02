"""Device identity verification (HMAC proof) for guest isolation.

Clients with a vault send:
  X-Device-Id: vk_<sha256(secret)[:40]>
  X-Device-Ts: unix seconds
  X-Device-Sign: hex(HMAC-SHA256(secret, f"{ts}.{METHOD}.{path}"))

Legacy bare `dev_*` IDs are **rejected** for cloud/account operations (forgeable).
Secrets are stored under DATA_DIR encrypted with OPENMAIL_MASTER_KEY.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from threading import Lock

from app.config import get_settings
from app.crypto import CryptoError, decrypt_str, encrypt_str, master_key_configured

logger = logging.getLogger("openmail.device_auth")

_registry: dict[str, str] = {}
_secrets: dict[str, bytes] = {}
_lock = Lock()
_loaded = False

_MAX_SKEW_SEC = 300


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _registry_path() -> Path:
    s = get_settings()
    env = os.environ.get("OPENMAIL_DEVICE_REGISTRY_PATH", "").strip()
    if env:
        return Path(env)
    data = os.environ.get("OPENMAIL_DATA_DIR", "").strip()
    if data:
        return Path(data) / "device_registry.json"
    db = (s.openmail_database_url or "").replace("sqlite:///", "")
    if db.startswith("/") and "openmail" in db:
        return Path(db).parent / "device_registry.json"
    return Path("data") / "device_registry.json"


def _b64decode_secret(raw: str) -> bytes:
    text = raw.strip()
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception:
        return base64.b64decode(text + pad)


def _persist_unlocked() -> None:
    if not master_key_configured():
        logger.warning("device registry not persisted: master key missing")
        return
    path = _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, str]] = []
        for pid, secret in _secrets.items():
            secret_b64 = base64.urlsafe_b64encode(secret).decode("ascii").rstrip("=")
            try:
                enc = encrypt_str(secret_b64)
            except CryptoError:
                logger.exception("encrypt device secret failed for %s", pid[:12])
                continue
            entries.append(
                {
                    "public_id": pid,
                    "secret_enc": enc,
                    "hash": _registry.get(pid) or _sha256_hex(secret),
                }
            )
        payload = {"v": 1, "entries": entries}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        logger.exception("failed to persist device registry")


def load_registry() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        path = _registry_path()
        if not path.is_file():
            _loaded = True
            return
        if not master_key_configured():
            logger.warning("device registry file present but master key missing")
            _loaded = True
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for ent in data.get("entries") or []:
                pid = str(ent.get("public_id") or "").strip()
                enc = ent.get("secret_enc")
                if not pid or not enc:
                    continue
                try:
                    secret_b64 = decrypt_str(str(enc))
                    secret = _b64decode_secret(secret_b64)
                except Exception:
                    logger.warning("skip corrupt device registry entry %s", pid[:16])
                    continue
                sh = _sha256_hex(secret)
                _secrets[pid] = secret
                _registry[pid] = sh
            logger.info("loaded %d device registrations", len(_secrets))
        except Exception:
            logger.exception("failed to load device registry")
        _loaded = True


def public_id_from_header(device_id: str | None) -> str | None:
    did = (device_id or "").strip()
    return did or None


def is_registered(public_id: str | None) -> bool:
    load_registry()
    if not public_id:
        return False
    with _lock:
        return public_id in _secrets


def register_device_secret(public_id: str, secret_b64: str) -> str:
    """Register device. public_id MUST equal vk_ + sha256(raw_secret)[:40].

    Security:
    - Never alias a mismatched client public_id (prevents takeover of other devices).
    - If canonical id already registered with a *different* secret → reject.
    - Same secret re-register is idempotent (OK).
    """
    load_registry()
    pid = (public_id or "").strip()
    try:
        secret = _b64decode_secret(secret_b64)
    except Exception as exc:
        raise ValueError("invalid secret encoding") from exc
    if len(secret) < 16:
        raise ValueError("secret too short")
    sh = _sha256_hex(secret)
    expected = f"vk_{sh[:40]}"

    # Client must send the canonical id (no free-form aliases)
    if not pid.startswith("vk_"):
        raise ValueError("public_id must be vk_<sha256(secret)[:40]>")
    if pid != expected:
        # Allow full-hash form vk_<64 hex> that starts with expected suffix
        if not (len(pid) >= 3 + 40 and pid[3:43] == sh[:40]):
            raise ValueError("public_id does not match secret")
        expected = f"vk_{sh[:40]}"

    with _lock:
        existing = _secrets.get(expected)
        if existing is not None and existing != secret:
            raise ValueError("device already registered")
        _secrets[expected] = secret
        _registry[expected] = sh
        # Do NOT store client-provided aliases
        _persist_unlocked()
    return expected


def verify_request(
    public_id: str | None,
    ts: str | None,
    signature: str | None,
    method: str,
    path: str,
    *,
    require_hmac: bool = True,
) -> tuple[bool, str | None]:
    """Verify device identity.

    When require_hmac=True (default for cloud ops):
      - Only vk_* registered devices with valid HMAC are accepted.
      - Forgeable legacy dev_* IDs are rejected.

    When require_hmac=False (proxy quota only):
      - Any non-empty device id of length >= 8 is accepted for rate limiting.
    """
    load_registry()
    pid = public_id_from_header(public_id)
    if not pid or len(pid) < 8:
        return False, "X-Device-Id required"

    if not require_hmac:
        return True, None

    # Reject forgeable legacy IDs for privileged operations
    if not pid.startswith("vk_"):
        return False, "vault device required (unlock vault / register vk_* device)"

    with _lock:
        secret = _secrets.get(pid)

    if secret is None:
        return False, "device not registered; POST /api/device/register"

    if not ts or not signature:
        return False, "X-Device-Ts and X-Device-Sign required"

    try:
        ts_i = int(ts)
    except ValueError:
        return False, "invalid timestamp"
    now = int(time.time())
    if abs(now - ts_i) > _MAX_SKEW_SEC:
        return False, "timestamp out of range"

    path_only = path.split("?", 1)[0]
    msg = f"{ts}.{method.upper()}.{path_only}".encode("utf-8")
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    sig = signature.strip()
    if not hmac.compare_digest(expected, sig.lower()) and not hmac.compare_digest(expected, sig):
        return False, "invalid device signature"
    return True, None


def require_device(
    *,
    public_id: str | None,
    ts: str | None,
    signature: str | None,
    method: str,
    path: str,
    require_hmac: bool = True,
) -> str:
    """Return device id or raise ValueError with message."""
    ok, err = verify_request(
        public_id=public_id,
        ts=ts,
        signature=signature,
        method=method,
        path=path,
        require_hmac=require_hmac,
    )
    if not ok:
        raise ValueError(err or "device proof failed")
    return (public_id or "").strip()
