"""Admin-only license issuance. Identity is OPENMAIL_ADMIN_DEVICE_IDS."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.crypto import decrypt_str
from app.deps import DbDep
from app.deps_device import device_id_admin
from app.models import LicenseCode, LicenseCodeUse
from app.services.license import issue_license_code

logger = logging.getLogger("openmail.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LicenseCreateBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class LicenseDeviceUseOut(BaseModel):
    device_id: str
    first_seen_at: datetime
    last_seen_at: datetime


class LicenseOut(BaseModel):
    id: str
    token: str
    note: str | None
    created_at: datetime
    created_by: str
    revoked_at: datetime | None
    device_count: int
    last_used_at: datetime | None
    devices: list[LicenseDeviceUseOut]


class LicenseListOut(BaseModel):
    ok: bool = True
    licenses: list[LicenseOut]


def _license_out(db, row: LicenseCode) -> LicenseOut:
    try:
        token = decrypt_str(row.token_enc)
    except Exception:
        logger.exception("license decrypt failed id=%s", row.id)
        token = ""
    uses = (
        db.query(LicenseCodeUse)
        .filter(LicenseCodeUse.token_hash == row.token_hash)
        .order_by(LicenseCodeUse.last_seen_at.desc())
        .all()
    )
    last_used = uses[0].last_seen_at if uses else None
    return LicenseOut(
        id=row.id,
        token=token,
        note=row.note,
        created_at=row.created_at,
        created_by=row.created_by,
        revoked_at=row.revoked_at,
        device_count=len(uses),
        last_used_at=last_used,
        devices=[
            LicenseDeviceUseOut(
                device_id=u.device_id,
                first_seen_at=u.first_seen_at,
                last_seen_at=u.last_seen_at,
            )
            for u in uses
        ],
    )


@router.get("/licenses", response_model=LicenseListOut)
def list_licenses(
    db: DbDep,
    _device_id: str = Depends(device_id_admin),
) -> LicenseListOut:
    rows = db.query(LicenseCode).order_by(LicenseCode.created_at.desc()).all()
    return LicenseListOut(ok=True, licenses=[_license_out(db, row) for row in rows])


@router.post("/licenses", response_model=LicenseOut)
def create_license(
    body: LicenseCreateBody,
    db: DbDep,
    device_id: str = Depends(device_id_admin),
) -> LicenseOut:
    try:
        row = issue_license_code(db, created_by=device_id, note=body.note)
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("license issue failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="could not issue license",
        ) from exc
    return _license_out(db, row)


@router.post("/licenses/{license_id}/revoke", response_model=LicenseOut)
def revoke_license(
    license_id: str,
    db: DbDep,
    device_id: str = Depends(device_id_admin),
) -> LicenseOut:
    row = db.get(LicenseCode, license_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="license not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        logger.info(
            "license revoked id=%s hash=%s by=%s",
            row.id,
            row.token_hash[:16],
            device_id[:16],
        )
    return _license_out(db, row)
