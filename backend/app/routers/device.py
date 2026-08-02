"""Device registration for HMAC-proof guest isolation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.device_auth import register_device_secret

router = APIRouter(prefix="/api/device", tags=["device"])


class DeviceRegisterBody(BaseModel):
    public_id: str = Field(..., min_length=8, max_length=128)
    secret_b64: str = Field(..., min_length=16, max_length=256)


class DeviceRegisterOut(BaseModel):
    ok: bool = True
    public_id: str


@router.post("/register", response_model=DeviceRegisterOut)
def register_device(body: DeviceRegisterBody) -> DeviceRegisterOut:
    """Register vault device secret (server stores secret only for HMAC verify; never returns it).

    Client sends public_id = vk_<sha256(secret)[:40]> and base64 secret once after unlock.
    """
    try:
        pid = register_device_secret(body.public_id, body.secret_b64)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return DeviceRegisterOut(ok=True, public_id=pid)
