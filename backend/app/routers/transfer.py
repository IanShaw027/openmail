"""QR device-to-device vault transfer (encrypted package, short-lived).

Flow:
1. Host creates a package (client-encrypted snapshot blob) → receives code + claim token.
2. Guest scans QR (or enters code) and claims with device id.
3. Host approves after explicit overwrite warning.
4. Guest downloads ciphertext once; server never sees vault keys or plaintext.

Server only stores opaque ciphertext + metadata. TTL ~10 minutes.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/transfer", tags=["transfer"])

# In-memory packages (single-node deploy). Restart clears pending transfers.
_LOCK = threading.Lock()
_PACKAGES: dict[str, "TransferPackage"] = {}

TTL_SECONDS = 10 * 60
MAX_BLOB_CHARS = 8_000_000  # ~8MB text (base64 of encrypted snapshot)


Direction = Literal["to_guest", "to_host"]


@dataclass
class TransferPackage:
    code: str
    blob: str
    direction: Direction
    host_device_id: str
    label: str
    created_at: float
    expires_at: float
    status: str = "pending"  # pending | claimed | approved | consumed | expired | rejected
    claim_token: str = ""
    guest_device_id: str | None = None
    claimed_at: float | None = None
    approved_at: float | None = None


def _purge_expired() -> None:
    now = time.time()
    dead = [c for c, p in _PACKAGES.items() if p.expires_at < now or p.status in ("consumed", "expired")]
    for c in dead:
        _PACKAGES.pop(c, None)


def _new_code() -> str:
    # 8 chars Crockford-ish (no O/0/I/1)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if code not in _PACKAGES:
            return code


class CreateBody(BaseModel):
    """Host uploads client-encrypted snapshot."""

    blob: str = Field(..., min_length=16, max_length=MAX_BLOB_CHARS)
    host_device_id: str = Field(..., min_length=4, max_length=128)
    direction: Direction = "to_guest"
    label: str = Field(default="", max_length=120)


class CreateOut(BaseModel):
    code: str
    claim_token: str
    expires_at: float
    direction: Direction
    # Deep link path for QR (SPA builds full URL)
    qr_path: str


class ClaimBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)
    guest_device_id: str = Field(..., min_length=4, max_length=128)


class ClaimOut(BaseModel):
    claim_token: str
    status: str
    direction: Direction
    label: str
    expires_at: float
    host_hint: str


class ApproveBody(BaseModel):
    code: str
    claim_token: str
    host_device_id: str
    approve: bool = True


class StatusOut(BaseModel):
    code: str
    status: str
    direction: Direction
    label: str
    expires_at: float
    guest_device_id: str | None = None
    has_blob: bool = False


class DownloadOut(BaseModel):
    blob: str
    direction: Direction
    label: str


@router.post("/create", response_model=CreateOut)
def create_package(body: CreateBody) -> CreateOut:
    with _LOCK:
        _purge_expired()
        if len(_PACKAGES) > 200:
            raise HTTPException(status_code=503, detail="Too many pending transfers")
        code = _new_code()
        claim = secrets.token_urlsafe(24)
        now = time.time()
        pkg = TransferPackage(
            code=code,
            blob=body.blob,
            direction=body.direction,
            host_device_id=body.host_device_id.strip(),
            label=(body.label or "").strip()[:120],
            created_at=now,
            expires_at=now + TTL_SECONDS,
            claim_token=claim,
            status="pending",
        )
        _PACKAGES[code] = pkg
        return CreateOut(
            code=code,
            claim_token=claim,
            expires_at=pkg.expires_at,
            direction=pkg.direction,
            qr_path=f"/transfer?code={code}",
        )


@router.post("/claim", response_model=ClaimOut)
def claim_package(body: ClaimBody) -> ClaimOut:
    code = body.code.strip().upper()
    with _LOCK:
        _purge_expired()
        pkg = _PACKAGES.get(code)
        if not pkg or pkg.expires_at < time.time():
            raise HTTPException(status_code=404, detail="Transfer not found or expired")
        if pkg.status in ("consumed", "rejected", "expired"):
            raise HTTPException(status_code=410, detail=f"Transfer {pkg.status}")
        if pkg.status == "pending":
            pkg.status = "claimed"
            pkg.guest_device_id = body.guest_device_id.strip()
            pkg.claimed_at = time.time()
        elif pkg.guest_device_id and pkg.guest_device_id != body.guest_device_id.strip():
            raise HTTPException(status_code=409, detail="Already claimed by another device")
        # Same guest re-claim OK
        host_hint = (pkg.host_device_id[:8] + "…") if pkg.host_device_id else ""
        return ClaimOut(
            claim_token=pkg.claim_token,
            status=pkg.status,
            direction=pkg.direction,
            label=pkg.label,
            expires_at=pkg.expires_at,
            host_hint=host_hint,
        )


@router.get("/status/{code}", response_model=StatusOut)
def package_status(code: str, claim_token: str = "") -> StatusOut:
    code = code.strip().upper()
    with _LOCK:
        _purge_expired()
        pkg = _PACKAGES.get(code)
        if not pkg:
            raise HTTPException(status_code=404, detail="Not found")
        if pkg.expires_at < time.time():
            pkg.status = "expired"
        # Host can poll with claim_token; public status omits guest id if wrong token
        guest = pkg.guest_device_id
        if claim_token and claim_token != pkg.claim_token:
            guest = None
        return StatusOut(
            code=pkg.code,
            status=pkg.status,
            direction=pkg.direction,
            label=pkg.label,
            expires_at=pkg.expires_at,
            guest_device_id=guest,
            has_blob=bool(pkg.blob) and pkg.status in ("approved", "claimed", "pending"),
        )


@router.post("/approve", response_model=StatusOut)
def approve_package(body: ApproveBody) -> StatusOut:
    code = body.code.strip().upper()
    with _LOCK:
        _purge_expired()
        pkg = _PACKAGES.get(code)
        if not pkg or pkg.expires_at < time.time():
            raise HTTPException(status_code=404, detail="Transfer not found or expired")
        if body.claim_token != pkg.claim_token:
            raise HTTPException(status_code=403, detail="Invalid claim token")
        if body.host_device_id.strip() != pkg.host_device_id:
            raise HTTPException(status_code=403, detail="Only host can approve")
        if pkg.status not in ("pending", "claimed", "approved"):
            raise HTTPException(status_code=409, detail=f"Cannot approve in status {pkg.status}")
        if not body.approve:
            pkg.status = "rejected"
            pkg.blob = ""
        else:
            if pkg.status == "pending" and not pkg.guest_device_id:
                # Allow pre-approve; guest still needs claim
                pass
            pkg.status = "approved"
            pkg.approved_at = time.time()
        return StatusOut(
            code=pkg.code,
            status=pkg.status,
            direction=pkg.direction,
            label=pkg.label,
            expires_at=pkg.expires_at,
            guest_device_id=pkg.guest_device_id,
            has_blob=bool(pkg.blob),
        )


@router.post("/download", response_model=DownloadOut)
def download_package(body: ClaimBody) -> DownloadOut:
    """Guest downloads ciphertext after host approval. One-shot."""
    code = body.code.strip().upper()
    with _LOCK:
        _purge_expired()
        pkg = _PACKAGES.get(code)
        if not pkg or pkg.expires_at < time.time():
            raise HTTPException(status_code=404, detail="Transfer not found or expired")
        if pkg.status != "approved":
            raise HTTPException(
                status_code=409,
                detail="Waiting for host approval" if pkg.status in ("pending", "claimed") else pkg.status,
            )
        if not pkg.guest_device_id:
            raise HTTPException(status_code=409, detail="Not claimed")
        if pkg.guest_device_id != body.guest_device_id.strip():
            raise HTTPException(status_code=403, detail="Device mismatch")
        blob = pkg.blob
        if not blob:
            raise HTTPException(status_code=410, detail="Blob already consumed")
        # One-shot
        pkg.blob = ""
        pkg.status = "consumed"
        return DownloadOut(blob=blob, direction=pkg.direction, label=pkg.label)
