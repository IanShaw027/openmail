"""The healthcheck must notice a database it can no longer write to.

Docker restarts the container on an unhealthy status, so a health endpoint that
only reports process liveness will happily keep a instance in the load path
while every real request 500s on a read-only data directory.
"""

from __future__ import annotations

import os
import stat

import app.db as app_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


def test_health_is_ok_when_the_database_accepts_writes(client: TestClient):
    resp = client.get("/api/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["database_writable"] is True


def test_health_reports_503_when_the_database_is_read_only(client: TestClient, tmp_path, monkeypatch):
    db_path = tmp_path / "ro.db"
    create_engine(f"sqlite:///{db_path}").connect().close()

    # Read-only *directory* as well: SQLite needs to create -wal/-journal sidecars
    # next to the file, which is exactly what breaks on an upgraded install.
    db_path.chmod(stat.S_IRUSR)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setattr(app_db, "engine", create_engine(f"sqlite:///{db_path}"))

    try:
        resp = client.get("/api/health")
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)
        db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["database_writable"] is False
