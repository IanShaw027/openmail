"""Public config + quota (no login)."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, Request

from app.deps import DbDep, SettingsDep
from app.models import Account
from app.services.device_auth import verify_request
from app.services.license import is_licensed, quota_snapshot

router = APIRouter(prefix="/api", tags=["config"])

_EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


@router.get("/config/public")
def public_config(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
    x_device_body_sha256: str | None = Header(default=None, alias="X-Device-Body-Sha256"),
    x_device_nonce: str | None = Header(default=None, alias="X-Device-Nonce"),
) -> dict:
    """Frontend bootstrap: quotas, flags, license status.

    cloud_used is only returned when the request presents a valid vault HMAC
    (prevents probing arbitrary device ids for account counts).
    """
    did = (x_device_id or "").strip() or None
    cloud_used: int | None = None
    # Only count cloud accounts for proven vault devices
    if did and did.startswith("vk_"):
        client_hash = (x_device_body_sha256 or "").strip().lower() or None
        body_sha256: str | None = None
        header_body_ok = True
        if client_hash is not None:
            # GET has empty body; header must claim sha256("") when present.
            if client_hash != _EMPTY_BODY_SHA256:
                header_body_ok = False
            else:
                body_sha256 = client_hash
        ok, _err = verify_request(
            did,
            x_device_ts,
            x_device_sign,
            request.method,
            f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path,
            require_hmac=True,
            body_sha256=body_sha256,
            nonce=x_device_nonce,
        )
        # Mismatched body-hash header must not authenticate even if legacy sig matches
        if ok and header_body_ok:
            cloud_used = (
                db.query(Account).filter(Account.owner_user_id == did).count()
            )
    snap = quota_snapshot(
        device_id=x_device_id if cloud_used is not None else None,
        license_token=x_license_token,
        settings=settings,
        cloud_used=cloud_used if cloud_used is not None else 0,
        db=db,
    )
    # Hide cloud_used from unauthenticated probes
    if cloud_used is None:
        snap = dict(snap)
        snap.pop("cloud_used", None)
        # Do not bind poll_used_hour to a forgeable id either
        snap["poll_used_hour"] = 0
    return {
        "ok": True,
        "auth_ui_enabled": settings.auth_ui_enabled,
        "device_admission": settings.device_admission,
        "fetch_concurrency": settings.fetch_concurrency,
        "fetch_lookback_days": settings.fetch_default_lookback_days,
        "mail_retention_days": settings.mail_retention_days,
        "quota": snap,
        "quota_defaults": {
            "max_local_accounts": settings.quota_max_local_accounts,
            "max_cloud_accounts": settings.quota_max_cloud_accounts,
            "max_poll_per_hour": settings.quota_max_poll_per_hour,
        },
        "licensed": is_licensed(
            device_id=x_device_id if cloud_used is not None else None,
            license_token=x_license_token,
            settings=settings,
            db=db,
        ),
    }
