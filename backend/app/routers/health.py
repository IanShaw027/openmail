"""Health check endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status

import app.db as app_db
from app import __version__
from app.config import get_settings
from app.crypto import master_key_configured
from app.schemas import HealthOut

router = APIRouter(tags=["health"])
logger = logging.getLogger("openmail.health")


def _database_writable() -> bool:
    """Can the database actually accept a write right now?

    Connectivity alone is not enough on SQLite: the failure worth catching is a
    data directory that becomes unwritable after startup (disk full, permissions
    changed, WAL sidecar refused), which leaves reads working while every real
    request 500s.

    Rewriting ``user_version`` to the value it already holds is the probe: it
    changes nothing, but it is a real write, so it fails on a read-only database
    the way an actual request would. ``BEGIN IMMEDIATE`` is not enough — SQLite
    takes the lock and defers the error until something is written.

    Uses a raw DBAPI connection so SQLAlchemy's implicit transaction handling
    cannot turn the probe into "transaction within a transaction".
    """
    engine = app_db.engine
    is_sqlite = engine.dialect.name == "sqlite"
    raw = None
    try:
        raw = engine.raw_connection()
        cur = raw.cursor()
        try:
            if is_sqlite:
                version = cur.execute("PRAGMA user_version").fetchone()[0]
                cur.execute(f"PRAGMA user_version={int(version)}")
            else:
                cur.execute("SELECT 1")
        finally:
            cur.close()
        raw.commit()
        return True
    except Exception:
        logger.warning("health: database is not writable", exc_info=True)
        return False
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:
                logger.debug("health: closing probe connection failed", exc_info=True)


@router.get("/health", response_model=HealthOut)
@router.get("/api/health", response_model=HealthOut)
def health(response: Response) -> HealthOut:
    settings = get_settings()
    writable = _database_writable()
    if not writable:
        # The container healthcheck reads the status code, so a degraded process
        # has to stop reporting 200 or it keeps taking traffic it cannot serve.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        ok=writable,
        version=__version__,
        master_key_configured=master_key_configured(settings),
        database_writable=writable,
    )
