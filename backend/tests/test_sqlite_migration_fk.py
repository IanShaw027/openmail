"""Regression: the SQLite migration must actually run with foreign keys off.

`PRAGMA foreign_keys` is a silent no-op inside a transaction, so issuing it
after `engine.begin()` left enforcement ON while the migration dropped the
legacy `users` table that `accounts.owner_user_id` still referenced.
"""

from __future__ import annotations

import sqlite3

import app.db as db_mod
from sqlalchemy import create_engine, event

LEGACY_SCHEMA = """
CREATE TABLE users (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);
CREATE TABLE user_sessions (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    user_id VARCHAR(40) REFERENCES users(id)
);
CREATE TABLE app_settings (
    key VARCHAR(64) NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO app_settings (key, value) VALUES ('admin_password', '"hunter2"');
INSERT INTO app_settings (key, value) VALUES ('cookie_secure', 'true');
INSERT INTO app_settings (key, value) VALUES ('sync_concurrency', '4');
CREATE TABLE accounts (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    pool VARCHAR(32) NOT NULL,
    owner_user_id VARCHAR(128) REFERENCES users(id),
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
INSERT INTO users (id, email) VALUES ('u1', 'owner@example.com');
INSERT INTO user_sessions (id, user_id) VALUES ('s1', 'u1');
INSERT INTO accounts (id, email, provider, pool, owner_user_id, status, created_at, updated_at)
VALUES ('a1', 'box@example.com', 'imap', 'default', 'u1', 'active',
        '2026-01-01 00:00:00', '2026-01-01 00:00:00');
"""


def _make_legacy_db(path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(LEGACY_SCHEMA)
        con.commit()
    finally:
        con.close()


def _engine_with_fk_on(path):
    """Mirror the real engine: every connection starts with foreign keys ON."""
    engine = create_engine(f"sqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def test_migration_drops_legacy_users_table_with_referencing_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)
    engine = _engine_with_fk_on(db_path)
    monkeypatch.setattr(db_mod, "engine", engine)

    db_mod.migrate_schema()

    con = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "users" not in tables
        assert "user_sessions" not in tables
        assert "accounts" in tables

        # The account row survived the rebuild, owner id intact.
        row = con.execute("SELECT id, email, owner_user_id FROM accounts").fetchone()
        assert row == ("a1", "box@example.com", "u1")

        # No dangling FK to the dropped table.
        fks = con.execute("PRAGMA foreign_key_list(accounts)").fetchall()
        assert not any(str(f[2]).lower() == "users" for f in fks), fks
    finally:
        con.close()

    # Enforcement is restored for connections handed out after the migration.
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_migration_purges_overrides_for_settings_that_no_longer_exist(tmp_path, monkeypatch):
    """A stored admin password must not outlive the setting that used it."""
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)
    monkeypatch.setattr(db_mod, "engine", _engine_with_fk_on(db_path))

    db_mod.migrate_schema()

    con = sqlite3.connect(db_path)
    try:
        keys = {r[0] for r in con.execute("SELECT key FROM app_settings")}
    finally:
        con.close()

    assert "admin_password" not in keys
    assert "cookie_secure" not in keys
    # Live overrides are untouched.
    assert "sync_concurrency" in keys


def test_postgres_drops_users_fk_before_dropping_table():
    """Postgres cannot DROP TABLE users while accounts.owner_user_id still FKs it."""
    executed: list[str] = []

    class _Result:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, stmt, *args, **kwargs):  # type: ignore[no-untyped-def]
            executed.append(str(stmt))
            return _Result()

    class _Insp:
        def get_foreign_keys(self, table):  # type: ignore[no-untyped-def]
            assert table == "accounts"
            return [
                {
                    "name": "accounts_owner_user_id_fkey",
                    "referred_table": "users",
                    "constrained_columns": ["owner_user_id"],
                }
            ]

    db_mod._drop_legacy_user_tables(
        _Conn(),
        _Insp(),
        dialect="postgresql",
        tables={"accounts", "users", "user_sessions"},
    )
    joined = "\n".join(executed)
    assert "DROP CONSTRAINT" in joined.upper()
    assert "accounts_owner_user_id_fkey" in joined
    assert joined.upper().index("DROP CONSTRAINT") < joined.upper().index("DROP TABLE")
    assert "users" in joined.lower()


def test_generic_migrate_widens_fetch_lock_account_id(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    executed: list[str] = []
    conn = MagicMock()
    conn.execute.side_effect = lambda stmt, *a, **k: executed.append(str(stmt))

    class _Insp:
        def get_table_names(self):
            return ["accounts", "fetch_lock_state"]

        def get_columns(self, table):
            if table == "accounts":
                return [
                    {"name": "id", "type": SimpleNamespace(length=40)},
                    {"name": "owner_user_id", "type": SimpleNamespace(length=128)},
                    {"name": "latest_verification_code", "type": SimpleNamespace(length=None)},
                ]
            if table == "fetch_lock_state":
                return [
                    {"name": "account_id", "type": SimpleNamespace(length=40)},
                    {"name": "lease_token", "type": SimpleNamespace(length=36)},
                ]
            return []

        def get_foreign_keys(self, table):
            return []

    monkeypatch.setattr(db_mod, "inspect", lambda _conn: _Insp())
    monkeypatch.setattr(db_mod.engine, "dialect", SimpleNamespace(name="postgresql"))
    db_mod._generic_add_missing_columns(conn)
    joined = "\n".join(executed)
    assert "ALTER TABLE fetch_lock_state ALTER COLUMN account_id TYPE VARCHAR(80)" in joined
