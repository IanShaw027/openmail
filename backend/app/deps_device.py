"""Shared FastAPI helpers for device HMAC proof."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.services.device_auth import require_device as _require_device


def _valid_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


async def _body_sha256_for_request(
    request: Request,
    x_device_body_sha256: str | None,
) -> str | None:
    """Return body hash to bind into HMAC, or None for legacy path-only form.

    If the client sends ``X-Device-Body-Sha256``, read the raw body, require an
    exact match, and return the hash (included in the signed message).

    Without the header, return None. ``verify_request`` then allows legacy
    path-only HMAC only for GET/HEAD; DELETE and other mutating methods still
    require body hash (use sha256 of empty body when there is no payload).
    Starlette caches the body after the first ``await request.body()``.
    """
    client_raw = (x_device_body_sha256 or "").strip().lower() or None

    # No client body-hash header → None; GET/HEAD may use path-only HMAC,
    # DELETE/POST/PUT/PATCH require the header in verify_request.
    if client_raw is None:
        return None

    # Client claims a body hash: bind to raw wire bytes (Starlette caches after read).
    body = await request.body()
    actual = hashlib.sha256(body).hexdigest()

    if not _valid_sha256_hex(client_raw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid X-Device-Body-Sha256",
        )
    if not hmac.compare_digest(client_raw, actual):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Device-Body-Sha256 mismatch",
        )
    return actual


async def device_id_strict(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
    x_device_body_sha256: str | None = Header(default=None, alias="X-Device-Body-Sha256"),
    x_device_nonce: str | None = Header(default=None, alias="X-Device-Nonce"),
) -> str:
    """Require registered vault device + valid HMAC (cloud / stored credentials)."""
    try:
        body_sha256 = await _body_sha256_for_request(request, x_device_body_sha256)
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        return _require_device(
            public_id=x_device_id,
            ts=x_device_ts,
            signature=x_device_sign,
            method=request.method,
            path=path,
            require_hmac=True,
            body_sha256=body_sha256,
            nonce=x_device_nonce,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e


async def device_id_quota(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
    x_device_body_sha256: str | None = Header(default=None, alias="X-Device-Body-Sha256"),
    x_device_nonce: str | None = Header(default=None, alias="X-Device-Nonce"),
) -> str:
    """Device id for proxy fetch/send — same as strict (vault + HMAC required).

    Random/legacy device IDs are rejected so open proxy cannot be abused as
    an anonymous mail relay without unlocking a registered vault.
    """
    return await device_id_strict(
        request,
        x_device_id=x_device_id,
        x_device_ts=x_device_ts,
        x_device_sign=x_device_sign,
        x_device_body_sha256=x_device_body_sha256,
        x_device_nonce=x_device_nonce,
    )


async def device_id_any(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
    x_device_body_sha256: str | None = Header(default=None, alias="X-Device-Body-Sha256"),
    x_device_nonce: str | None = Header(default=None, alias="X-Device-Nonce"),
) -> str:
    """Registered vault device with valid HMAC — trusted or still pending.

    Used by ``GET /api/device/me`` so a waiting device can learn its status
    without being able to call privileged APIs.
    """
    try:
        body_sha256 = await _body_sha256_for_request(request, x_device_body_sha256)
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        return _require_device(
            public_id=x_device_id,
            ts=x_device_ts,
            signature=x_device_sign,
            method=request.method,
            path=path,
            require_hmac=True,
            require_trusted=False,
            body_sha256=body_sha256,
            nonce=x_device_nonce,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e


async def device_id_admin(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
    x_device_body_sha256: str | None = Header(default=None, alias="X-Device-Body-Sha256"),
    x_device_nonce: str | None = Header(default=None, alias="X-Device-Nonce"),
) -> str:
    """Trusted vault device that is listed in OPENMAIL_ADMIN_DEVICE_IDS.

    Empty allowlist fails closed (nobody is admin). Pending devices never
    reach this check — ``device_id_strict`` requires trusted status first.
    """
    did = await device_id_strict(
        request,
        x_device_id=x_device_id,
        x_device_ts=x_device_ts,
        x_device_sign=x_device_sign,
        x_device_body_sha256=x_device_body_sha256,
        x_device_nonce=x_device_nonce,
    )
    allowed = get_settings().admin_device_id_set
    if not allowed or did not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin device required",
        )
    return did
