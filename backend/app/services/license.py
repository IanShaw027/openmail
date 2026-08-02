"""Device fingerprint + license token checks (no user accounts)."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from threading import Lock
from typing import Any

from app.config import Settings, get_settings

# device_id -> list of fetch timestamps (unix)
_poll_log: dict[str, list[float]] = defaultdict(list)
_poll_lock = Lock()


def is_licensed(
    *,
    device_id: str | None,
    license_token: str | None,
    settings: Settings | None = None,
) -> bool:
    """Return True if request presents a valid unlimited license."""
    s = settings or get_settings()
    token = (license_token or "").strip()
    if not token:
        return False
    if token in s.license_token_set:
        return True
    secret = (s.license_hmac_secret or "").strip()
    fp = (device_id or "").strip()
    if secret and fp:
        expected = hmac.new(
            secret.encode("utf-8"),
            fp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, token.lower()) or hmac.compare_digest(
            expected, token
        ):
            return True
    return False


def check_poll_quota(
    device_id: str | None,
    *,
    license_token: str | None = None,
    settings: Settings | None = None,
) -> tuple[bool, str | None]:
    """Return (allowed, error_message). Licensed devices skip limits."""
    s = settings or get_settings()
    if is_licensed(device_id=device_id, license_token=license_token, settings=s):
        return True, None
    did = (device_id or "anonymous").strip() or "anonymous"
    limit = max(1, int(s.quota_max_poll_per_hour or 120))
    now = time.time()
    window = 3600.0
    with _poll_lock:
        arr = _poll_log[did]
        arr[:] = [t for t in arr if now - t < window]
        if len(arr) >= limit:
            return False, f"poll quota exceeded ({limit}/hour); use a license token"
        arr.append(now)
    return True, None


def quota_snapshot(
    *,
    device_id: str | None = None,
    license_token: str | None = None,
    settings: Settings | None = None,
    cloud_used: int | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    licensed = is_licensed(device_id=device_id, license_token=license_token, settings=s)
    did = (device_id or "anonymous").strip() or "anonymous"
    now = time.time()
    with _poll_lock:
        arr = [t for t in _poll_log.get(did, []) if now - t < 3600]
        used = len(arr)
    out: dict[str, Any] = {
        "licensed": licensed,
        "max_local_accounts": None if licensed else s.quota_max_local_accounts,
        "max_cloud_accounts": None if licensed else s.quota_max_cloud_accounts,
        "max_poll_per_hour": None if licensed else s.quota_max_poll_per_hour,
        "poll_used_hour": used,
        "fetch_lookback_days": s.fetch_default_lookback_days,
        "mail_retention_days": s.mail_retention_days,
        "auth_ui_enabled": s.auth_ui_enabled,
    }
    if cloud_used is not None:
        out["cloud_used"] = cloud_used
    return out
