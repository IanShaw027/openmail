"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.crypto import master_key_configured
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
@router.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        ok=True,
        version=__version__,
        master_key_configured=master_key_configured(settings),
    )
