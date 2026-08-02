"""Mail search API — removed (local mailCache + /mails UI)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["mails"])


@router.get("/api/me/mails/search")
@router.get("/api/me/mails/recent")
def mail_search_gone() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="server mail search removed — use the local Mails page after fetching",
    )
