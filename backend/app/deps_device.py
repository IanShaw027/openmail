"""Shared FastAPI helpers for device HMAC proof."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from app.services.device_auth import require_device as _require_device


def device_id_strict(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
) -> str:
    """Require registered vault device + valid HMAC (cloud / stored credentials)."""
    try:
        return _require_device(
            public_id=x_device_id,
            ts=x_device_ts,
            signature=x_device_sign,
            method=request.method,
            path=request.url.path,
            require_hmac=True,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from e


def device_id_quota(
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_ts: str | None = Header(default=None, alias="X-Device-Ts"),
    x_device_sign: str | None = Header(default=None, alias="X-Device-Sign"),
) -> str:
    """Device id for proxy fetch/send — same as strict (vault + HMAC required).

    Random/legacy device IDs are rejected so open proxy cannot be abused as
    an anonymous mail relay without unlocking a registered vault.
    """
    return device_id_strict(
        request,
        x_device_id=x_device_id,
        x_device_ts=x_device_ts,
        x_device_sign=x_device_sign,
    )
