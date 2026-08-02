"""User-scoped sync HTTP API removed (background SyncWorker may still run)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["sync"])


@router.post("/api/me/sync")
def trigger_my_sync() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="user sync removed — fetch from console; local cache powers search",
    )
