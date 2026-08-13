"""Device registration and first-trust admission."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps_device import device_id_any, device_id_strict
from app.services import device_auth

router = APIRouter(prefix="/api/device", tags=["device"])


class DeviceRegisterBody(BaseModel):
    public_id: str = Field(..., min_length=8, max_length=128)
    secret_b64: str = Field(..., min_length=16, max_length=256)


class DeviceRegisterOut(BaseModel):
    ok: bool = True
    public_id: str
    status: str
    admission: str


class DeviceMeOut(BaseModel):
    ok: bool = True
    public_id: str
    status: str
    admission: str
    is_admin: bool = False


class DeviceListOut(BaseModel):
    ok: bool = True
    admission: str
    devices: list[dict]


class DeviceTargetBody(BaseModel):
    public_id: str = Field(..., min_length=8, max_length=128)


class StatusOut(BaseModel):
    ok: bool = True
    status: str | None = None


@router.post("/register", response_model=DeviceRegisterOut)
def register_device(request: Request, body: DeviceRegisterBody) -> DeviceRegisterOut:
    """Register vault device secret (server stores secret only for HMAC verify).

    Under ``first_trust`` admission the first device is trusted automatically;
    later devices return ``status=pending`` and cannot call privileged APIs
    until a trusted device approves them.
    """
    try:
        device_auth.note_register_attempt(_client_ip(request))
        pid = device_auth.register_device_secret(body.public_id, body.secret_b64)
    except ValueError as e:
        msg = str(e)
        if "rate limit" in msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=msg,
                headers={"Retry-After": "60"},
            ) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    st = device_auth.device_status(pid) or device_auth.STATUS_TRUSTED
    return DeviceRegisterOut(
        ok=True,
        public_id=pid,
        status=st,
        admission=get_settings().device_admission,
    )


@router.get("/me", response_model=DeviceMeOut)
def device_me(device_id: str = Depends(device_id_any)) -> DeviceMeOut:
    st = device_auth.device_status(device_id) or device_auth.STATUS_TRUSTED
    admin_ids = get_settings().admin_device_id_set
    is_admin = st == device_auth.STATUS_TRUSTED and bool(admin_ids) and device_id in admin_ids
    return DeviceMeOut(
        ok=True,
        public_id=device_id,
        status=st,
        admission=get_settings().device_admission,
        is_admin=is_admin,
    )


@router.get("/list", response_model=DeviceListOut)
def list_devices(_device_id: str = Depends(device_id_strict)) -> DeviceListOut:
    """List registered devices. Trusted devices only."""
    return DeviceListOut(
        ok=True,
        admission=get_settings().device_admission,
        devices=device_auth.list_devices(),
    )


@router.post("/approve", response_model=StatusOut)
def approve_device(
    body: DeviceTargetBody,
    device_id: str = Depends(device_id_strict),
) -> StatusOut:
    try:
        st = device_auth.approve_device(body.public_id, actor_id=device_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return StatusOut(ok=True, status=st)


@router.post("/reject", response_model=StatusOut)
def reject_device(
    body: DeviceTargetBody,
    device_id: str = Depends(device_id_strict),
) -> StatusOut:
    try:
        device_auth.reject_device(body.public_id, actor_id=device_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return StatusOut(ok=True, status="rejected")


@router.post("/revoke", response_model=StatusOut)
def revoke_device(
    body: DeviceTargetBody,
    device_id: str = Depends(device_id_strict),
) -> StatusOut:
    try:
        device_auth.revoke_device(body.public_id, actor_id=device_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return StatusOut(ok=True, status="revoked")


def _client_ip(request: Request) -> str:
    """Peer address. Ignores X-Forwarded-For (attacker-controlled)."""
    client = request.client
    return client.host if client and client.host else "unknown"
