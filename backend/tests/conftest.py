"""Pytest fixtures: isolated SQLite DB + TestClient."""

from __future__ import annotations

import base64
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Configure env before app imports settings cache
os.environ["OPENMAIL_MASTER_KEY"] = base64.b64encode(os.urandom(32)).decode()
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["OPENMAIL_DATABASE_URL"] = "sqlite://"
os.environ["COOKIE_SECURE"] = "false"
# Keep background sync quiet / short interval in tests (worker still starts)
os.environ.setdefault("SYNC_ENABLED_GLOBAL", "false")
os.environ.setdefault("SYNC_INTERVAL_SECONDS", "3600")

from app.config import get_settings
from app.db import Base, get_db
from app.main import create_app
import app.db as app_db
import app.models  # noqa: F401 — register all tables on Base.metadata before create_all
import app.services.sync_worker as sync_worker_mod


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    # Rebind process-wide SessionLocal so SyncWorker / background code share test DB
    old_engine = app_db.engine
    old_session = app_db.SessionLocal
    app_db.engine = engine
    app_db.SessionLocal = TestingSessionLocal
    sync_worker_mod.SessionLocal = TestingSessionLocal

    def _override_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    application = create_app()
    application.dependency_overrides[get_db] = _override_db

    with TestClient(application) as c:
        # Attach helpers for tests that need direct DB access
        c.db_session_factory = TestingSessionLocal  # type: ignore[attr-defined]
        yield c

    application.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    app_db.engine = old_engine
    app_db.SessionLocal = old_session
    sync_worker_mod.SessionLocal = old_session
    get_settings.cache_clear()


@pytest.fixture()
def db_session(client: TestClient) -> Generator[Session, None, None]:
    factory = client.db_session_factory  # type: ignore[attr-defined]
    db = factory()
    try:
        yield db
    finally:
        db.close()
