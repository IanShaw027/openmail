"""Device identity verification (HMAC proof) for guest isolation.

Clients with a vault send:
  X-Device-Id: vk_<sha256(secret)[:40]>
  X-Device-Ts: unix seconds
  X-Device-Body-Sha256: lowercase hex(SHA-256(raw body bytes))
  X-Device-Nonce: optional unique request id (bound into the HMAC)
  X-Device-Sign: hex(HMAC-SHA256(secret, f"{ts}.{METHOD}.{path}.{body_sha256}[.{nonce}]"))

Empty body (GET/HEAD/DELETE without payload): sha256 of empty string.
Legacy bare `dev_*` IDs are **rejected** for cloud/account operations (forgeable).
Secrets are stored under DATA_DIR encrypted with OPENMAIL_MASTER_KEY.

Backward compatibility: if X-Device-Body-Sha256 is absent, old signature
format ``{ts}.{METHOD}.{path}`` is still accepted for GET/HEAD only.
Mutating methods (POST/PUT/PATCH/DELETE/…) require the body-hash header when
HMAC is required (use sha256 of empty body when there is no payload).
A nonce, when sent, is appended to the signed message so two legitimate
requests in the same second are not treated as replays.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows / non-POSIX
    fcntl = None  # type: ignore[assignment]

from app.config import get_settings
from app.crypto import CryptoError, decrypt_str, encrypt_str, master_key_configured

logger = logging.getLogger("openmail.device_auth")

_registry: dict[str, str] = {}
_secrets: dict[str, bytes] = {}
# public_id → "trusted" | "pending". Missing on disk means trusted (upgrade path).
_status: dict[str, str] = {}
_created_at: dict[str, float] = {}
_lock = Lock()
_loaded = False
# HMAC replay cache: key → expiry unix seconds (mutating methods only).
_seen_hmac: dict[str, float] = {}
# POST /register attempts: ip → timestamps
_register_by_ip: dict[str, list[float]] = {}

_MAX_SKEW_SEC = 300
STATUS_TRUSTED = "trusted"
STATUS_PENDING = "pending"
_ADMISSION_OPEN = "open"
_ADMISSION_FIRST_TRUST = "first_trust"
MAX_PENDING_DEVICES = 32
REGISTER_MAX_PER_IP = 20
REGISTER_WINDOW_SEC = 3600
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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


def _admission_mode() -> str:
    try:
        return get_settings().device_admission
    except Exception:
        return _ADMISSION_FIRST_TRUST


def _normalize_status(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s == STATUS_PENDING:
        return STATUS_PENDING
    # Missing / unknown / legacy → trusted so upgrades do not lock out
    # devices that were already using the instance under open registration.
    return STATUS_TRUSTED


def _read_registry_file_unlocked(*, strict: bool = False) -> None:
    """Merge the current on-disk registry into this worker's cache."""
    path = _registry_path()
    if not path.is_file() or not master_key_configured():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for ent in data.get("entries") or []:
            pid = str(ent.get("public_id") or "").strip()
            enc = ent.get("secret_enc")
            if not pid or not enc:
                continue
            try:
                secret = _b64decode_secret(decrypt_str(str(enc)))
            except Exception as exc:
                logger.warning("skip corrupt device registry entry %s", pid[:16])
                if strict:
                    raise RuntimeError("corrupt device registry entry") from exc
                continue
            _secrets[pid] = secret
            _registry[pid] = _sha256_hex(secret)
            _status[pid] = _normalize_status(ent.get("status"))
            try:
                _created_at[pid] = float(ent.get("created_at") or 0) or time.time()
            except (TypeError, ValueError):
                _created_at[pid] = time.time()
    except Exception as exc:
        logger.exception("failed to load device registry")
        if strict:
            raise RuntimeError("failed to load device registry") from exc


@contextmanager
def _registry_file_lock() -> Iterator[None]:
    """Serialize read-merge-write across application workers.

    Uses ``fcntl.flock`` on POSIX. On platforms without fcntl (Windows), falls
    back to the in-process ``_lock`` only — multi-worker Windows deploys need
    an external single-writer or a different registry backend.
    """
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        with _lock:
            yield
        return
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _persist_unlocked() -> None:
    if not master_key_configured():
        raise RuntimeError("device registry not persisted: master key missing")
    path = _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        for pid, secret in _secrets.items():
            secret_b64 = base64.urlsafe_b64encode(secret).decode("ascii").rstrip("=")
            try:
                enc = encrypt_str(secret_b64)
            except CryptoError as exc:
                logger.exception("encrypt device secret failed for %s", pid[:12])
                raise RuntimeError("failed to encrypt device registry entry") from exc
            entries.append(
                {
                    "public_id": pid,
                    "secret_enc": enc,
                    "hash": _registry.get(pid) or _sha256_hex(secret),
                    "status": _status.get(pid) or STATUS_TRUSTED,
                    "created_at": _created_at.get(pid) or time.time(),
                }
            )
        payload = {"v": 2, "entries": entries}
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:
        logger.exception("failed to persist device registry")
        raise RuntimeError("failed to persist device registry") from exc


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
            raise RuntimeError("device registry present but master key missing")
        # Fail closed: a decrypt miss must not look like an empty registry
        # (first_trust would otherwise auto-trust a new device).
        _read_registry_file_unlocked(strict=True)
        logger.info("loaded %d device registrations", len(_secrets))
        _loaded = True


def rewrite_registry_with_primary_key() -> int:
    """Re-encrypt on-disk secrets under the current primary master key.

    Decrypt uses OPENMAIL_MASTER_KEY_FALLBACKS when needed. Returns the number
    of entries rewritten. Raises if the file exists but cannot be decrypted.
    """
    load_registry()
    with _lock, _registry_file_lock():
        _read_registry_file_unlocked(strict=True)
        n = len(_secrets)
        if n:
            _persist_unlocked()
        return n


def public_id_from_header(device_id: str | None) -> str | None:
    did = (device_id or "").strip()
    return did or None


def is_registered(public_id: str | None) -> bool:
    load_registry()
    if not public_id:
        return False
    with _lock:
        if public_id not in _secrets:
            _read_registry_file_unlocked()
        return public_id in _secrets


def device_status(public_id: str | None) -> str | None:
    """Return trusted/pending for a registered device, or None if unknown."""
    load_registry()
    pid = (public_id or "").strip()
    if not pid:
        return None
    with _lock:
        if pid not in _secrets:
            _read_registry_file_unlocked()
        if pid not in _secrets:
            return None
        return _status.get(pid) or STATUS_TRUSTED


def list_devices() -> list[dict[str, object]]:
    """Return registry rows without secrets (caller must already be trusted)."""
    load_registry()
    with _lock:
        _read_registry_file_unlocked()
        rows = []
        for pid in sorted(_secrets.keys(), key=lambda p: _created_at.get(p, 0)):
            rows.append(
                {
                    "public_id": pid,
                    "status": _status.get(pid) or STATUS_TRUSTED,
                    "created_at": _created_at.get(pid) or 0,
                }
            )
        return rows


def _count_trusted_unlocked() -> int:
    return sum(1 for pid in _secrets if (_status.get(pid) or STATUS_TRUSTED) == STATUS_TRUSTED)


def approve_device(target_id: str, *, actor_id: str) -> str:
    """Mark a pending device trusted. Actor must itself be trusted."""
    load_registry()
    target = (target_id or "").strip()
    actor = (actor_id or "").strip()
    if not target.startswith("vk_") or not actor.startswith("vk_"):
        raise ValueError("vault device id required")
    with _lock, _registry_file_lock():
        _read_registry_file_unlocked(strict=True)
        if (_status.get(actor) or STATUS_TRUSTED) != STATUS_TRUSTED or actor not in _secrets:
            raise ValueError("only a trusted device can approve")
        if target not in _secrets:
            raise ValueError("device not found")
        _status[target] = STATUS_TRUSTED
        _persist_unlocked()
    return STATUS_TRUSTED


def reject_device(target_id: str, *, actor_id: str) -> None:
    """Remove a pending registration. Actor must be trusted."""
    load_registry()
    target = (target_id or "").strip()
    actor = (actor_id or "").strip()
    with _lock, _registry_file_lock():
        _read_registry_file_unlocked(strict=True)
        if (_status.get(actor) or STATUS_TRUSTED) != STATUS_TRUSTED or actor not in _secrets:
            raise ValueError("only a trusted device can reject")
        if target not in _secrets:
            raise ValueError("device not found")
        if (_status.get(target) or STATUS_TRUSTED) != STATUS_PENDING:
            raise ValueError("only pending devices can be rejected; revoke a trusted device instead")
        if target == actor:
            raise ValueError("cannot reject self")
        _secrets.pop(target, None)
        _registry.pop(target, None)
        _status.pop(target, None)
        _created_at.pop(target, None)
        _persist_unlocked()


def revoke_device(target_id: str, *, actor_id: str) -> None:
    """Remove a trusted device. Refuses to remove the last trusted device."""
    load_registry()
    target = (target_id or "").strip()
    actor = (actor_id or "").strip()
    with _lock, _registry_file_lock():
        _read_registry_file_unlocked(strict=True)
        if (_status.get(actor) or STATUS_TRUSTED) != STATUS_TRUSTED or actor not in _secrets:
            raise ValueError("only a trusted device can revoke")
        if target not in _secrets:
            raise ValueError("device not found")
        if (_status.get(target) or STATUS_TRUSTED) == STATUS_PENDING:
            raise ValueError("use reject for pending devices")
        if _count_trusted_unlocked() <= 1 and (_status.get(target) or STATUS_TRUSTED) == STATUS_TRUSTED:
            raise ValueError("cannot revoke the last trusted device")
        _secrets.pop(target, None)
        _registry.pop(target, None)
        _status.pop(target, None)
        _created_at.pop(target, None)
        _persist_unlocked()


def register_device_secret(public_id: str, secret_b64: str) -> str:
    """Register device. public_id MUST equal vk_ + sha256(raw_secret)[:40].

    Security:
    - Never alias a mismatched client public_id (prevents takeover of other devices).
    - If canonical id already registered with a *different* secret → reject.
    - Same secret re-register is idempotent (OK).

    Admission (``OPENMAIL_DEVICE_ADMISSION``):
    - ``open``: every successful register is trusted (previous behaviour).
    - ``first_trust`` (default): the first device on an empty registry is
      trusted; later devices land as ``pending`` until a trusted device
      approves them. Existing on-disk entries without a status are treated
      as trusted so upgrades do not brick multi-device installs.
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

    with _lock, _registry_file_lock():
        # Another worker may have registered devices since this process started.
        _read_registry_file_unlocked(strict=True)
        existing = _secrets.get(expected)
        if existing is not None and existing != secret:
            raise ValueError("device already registered")
        if existing is not None:
            # Idempotent re-register: keep the existing status.
            return expected

        mode = _admission_mode()
        if mode == _ADMISSION_OPEN:
            status = STATUS_TRUSTED
        else:
            # first_trust: empty registry → bootstrap; otherwise wait for approval.
            status = STATUS_TRUSTED if _count_trusted_unlocked() == 0 else STATUS_PENDING

        if status == STATUS_PENDING:
            pending_n = sum(1 for s in _status.values() if s == STATUS_PENDING)
            if pending_n >= MAX_PENDING_DEVICES:
                raise ValueError("too many pending devices")

        _secrets[expected] = secret
        _registry[expected] = sh
        _status[expected] = status
        _created_at[expected] = time.time()
        _persist_unlocked()
        logger.info("device registered %s status=%s admission=%s", expected[:16], status, mode)
    return expected


def _normalize_body_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    h = value.strip().lower()
    if not h:
        return None
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        return None
    return h


def _normalize_nonce(value: str | None) -> str | None:
    if value is None:
        return None
    n = value.strip()
    if not n:
        return None
    if len(n) > 64 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in n
    ):
        return None
    return n


def verify_request(
    public_id: str | None,
    ts: str | None,
    signature: str | None,
    method: str,
    path: str,
    *,
    require_hmac: bool = True,
    require_trusted: bool = True,
    body_sha256: str | None = None,
    nonce: str | None = None,
) -> tuple[bool, str | None]:
    """Verify device identity.

    When require_hmac=True (default for cloud ops):
      - Only vk_* registered devices with valid HMAC are accepted.
      - Forgeable legacy dev_* IDs are rejected.
      - When require_trusted=True (default), pending devices are rejected so
        first-trust admission actually gates privileged APIs.
      - Signed message is ``{ts}.{METHOD}.{path}.{body_sha256}`` when
        ``body_sha256`` is provided (from X-Device-Body-Sha256 after server
        match against raw body bytes).
      - Without body hash: only GET/HEAD may use legacy ``{ts}.{METHOD}.{path}``.
        POST/PUT/PATCH require body hash binding.

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
            _read_registry_file_unlocked()
            secret = _secrets.get(pid)
        status = _status.get(pid) or STATUS_TRUSTED if secret is not None else None

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

    method_u = method.upper()
    request_path = path
    body_hash = _normalize_body_sha256(body_sha256)

    if body_sha256 is not None and body_hash is None:
        return False, "invalid X-Device-Body-Sha256"

    nonce_n = _normalize_nonce(nonce)
    if nonce is not None and str(nonce).strip() and nonce_n is None:
        return False, "invalid X-Device-Nonce"

    if body_hash is not None:
        msg_s = f"{ts}.{method_u}.{request_path}.{body_hash}"
        if nonce_n:
            msg_s = f"{msg_s}.{nonce_n}"
        msg = msg_s.encode("utf-8")
    elif method_u in ("GET", "HEAD"):
        # Backward compatible signature without body binding
        msg_s = f"{ts}.{method_u}.{request_path}"
        if nonce_n:
            msg_s = f"{msg_s}.{nonce_n}"
        msg = msg_s.encode("utf-8")
    else:
        # POST/PUT/PATCH/DELETE and other mutating methods require body hash
        # (sha256 of empty body is fine when there is no payload).
        return False, "X-Device-Body-Sha256 required"

    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    sig = signature.strip()
    try:
        sig_ok = hmac.compare_digest(expected, sig.lower())
        if not sig_ok and sig != sig.lower():
            sig_ok = hmac.compare_digest(expected, sig)
    except (ValueError, TypeError):
        return False, "invalid device signature"
    if not sig_ok:
        return False, "invalid device signature"

    if method_u in _MUTATING_METHODS:
        replay_key = (
            f"{pid}|{ts}|{method_u}|{request_path}|{body_hash or ''}|{sig.lower()}"
        )
        with _lock:
            now_f = float(now)
            expired = [k for k, exp in _seen_hmac.items() if exp <= now_f]
            for k in expired:
                _seen_hmac.pop(k, None)
            if replay_key in _seen_hmac:
                return False, "replayed device signature"
            _seen_hmac[replay_key] = now_f + _MAX_SKEW_SEC

    if require_trusted and status == STATUS_PENDING:
        return False, "device pending approval from a trusted device"
    return True, None


def note_register_attempt(client_ip: str | None) -> None:
    """Record an unauthenticated register hit. Raises ValueError if over cap."""
    ip = (client_ip or "unknown").strip() or "unknown"
    now = time.time()
    window_start = now - REGISTER_WINDOW_SEC
    with _lock:
        hits = [t for t in _register_by_ip.get(ip, []) if t > window_start]
        if len(hits) >= REGISTER_MAX_PER_IP:
            _register_by_ip[ip] = hits
            raise ValueError("register rate limit exceeded")
        hits.append(now)
        _register_by_ip[ip] = hits


def require_device(
    *,
    public_id: str | None,
    ts: str | None,
    signature: str | None,
    method: str,
    path: str,
    require_hmac: bool = True,
    require_trusted: bool = True,
    body_sha256: str | None = None,
    nonce: str | None = None,
) -> str:
    """Return device id or raise ValueError with message."""
    ok, err = verify_request(
        public_id=public_id,
        ts=ts,
        signature=signature,
        method=method,
        path=path,
        require_hmac=require_hmac,
        require_trusted=require_trusted,
        body_sha256=body_sha256,
        nonce=nonce,
    )
    if not ok:
        raise ValueError(err or "device proof failed")
    return (public_id or "").strip()
