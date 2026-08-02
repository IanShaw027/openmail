"""Database engine, session factory, and base model."""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger("openmail.db")


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sqlite_table_columns(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    return {str(r[1]) for r in rows}


def _sqlite_add_column(conn, table: str, column: str, col_type: str) -> None:  # type: ignore[no-untyped-def]
    cols = _sqlite_table_columns(conn, table)
    if column in cols:
        return
    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _sqlite_accounts_has_users_fk(conn) -> bool:  # type: ignore[no-untyped-def]
    """True if accounts.owner_user_id still references dropped users table."""
    try:
        rows = conn.exec_driver_sql("PRAGMA foreign_key_list(accounts)").fetchall()
    except Exception:
        return False
    for r in rows:
        # id, seq, table, from, to, on_update, on_delete, match
        if str(r[2]).lower() == "users" or str(r[3]).lower() == "owner_user_id":
            # owner_user_id FK to users (legacy) — must rebuild without it
            if str(r[2]).lower() == "users":
                return True
    return False


def _sqlite_rebuild_accounts_drop_users_fk(conn) -> None:  # type: ignore[no-untyped-def]
    """Recreate accounts without FK to users so device_id can be stored in owner_user_id."""
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS accounts_new (
            id VARCHAR(40) NOT NULL PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            pool VARCHAR(32) NOT NULL,
            owner_user_id VARCHAR(40),
            password_enc TEXT,
            credential_enc TEXT,
            tag VARCHAR(128),
            note TEXT,
            status VARCHAR(32) NOT NULL,
            last_fetch_at DATETIME,
            last_error VARCHAR(512),
            latest_verification_code VARCHAR(64),
            latest_code_at DATETIME,
            sync_enabled BOOLEAN NOT NULL DEFAULT 0,
            last_sync_at DATETIME,
            last_sync_error VARCHAR(512),
            proxy VARCHAR(512),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """
    )
    # Copy overlapping columns
    old_cols = _sqlite_table_columns(conn, "accounts")
    new_cols = _sqlite_table_columns(conn, "accounts_new")
    shared = [c for c in new_cols if c in old_cols]
    col_list = ", ".join(shared)
    conn.exec_driver_sql(
        f"INSERT INTO accounts_new ({col_list}) SELECT {col_list} FROM accounts"
    )
    conn.exec_driver_sql("DROP TABLE accounts")
    conn.exec_driver_sql("ALTER TABLE accounts_new RENAME TO accounts")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_accounts_email ON accounts (email)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_accounts_owner_user_id ON accounts (owner_user_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_accounts_pool ON accounts (pool)"
    )
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")


# Columns that may be missing on upgraded installs (create_all does not ALTER).
# dialect-agnostic ADD COLUMN statements — nullable / with default where needed.
_ACCOUNTS_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("last_sync_at", "TIMESTAMP"),
    ("last_sync_error", "VARCHAR(512)"),
    ("sync_enabled", "BOOLEAN DEFAULT FALSE"),
    ("proxy", "VARCHAR(512)"),
    ("latest_verification_code", "VARCHAR(64)"),
    ("latest_code_at", "TIMESTAMP"),
    ("last_fetch_at", "TIMESTAMP"),
    ("last_error", "VARCHAR(512)"),
]


def _generic_add_missing_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """For PostgreSQL/MySQL/etc.: ADD COLUMN IF NOT EXISTS style via inspector."""
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "accounts" not in tables:
        return
    existing = {c["name"] for c in insp.get_columns("accounts")}
    dialect = engine.dialect.name
    for col, col_type in _ACCOUNTS_EXTRA_COLUMNS:
        if col in existing:
            continue
        # SQLite-style types already work on PG for these simple cases
        sql_type = col_type
        if dialect == "postgresql":
            sql_type = col_type.replace("BOOLEAN DEFAULT FALSE", "BOOLEAN DEFAULT FALSE")
            sql_type = sql_type.replace("TIMESTAMP", "TIMESTAMP WITH TIME ZONE")
        try:
            if dialect in ("postgresql", "mysql", "mariadb"):
                conn.execute(text(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS {col} {sql_type}"))
            else:
                # best-effort plain ALTER (may fail if exists)
                conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {col} {sql_type}"))
            logger.info("migrate: added accounts.%s", col)
        except Exception:
            logger.exception("migrate: failed to add accounts.%s", col)

    # Drop legacy user tables if present (local-first no longer uses them)
    for tbl in ("user_sessions", "admin_sessions", "mail_index", "users"):
        if tbl in tables:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
                logger.info("migrate: dropped legacy table %s", tbl)
            except Exception:
                logger.exception("migrate: failed to drop %s", tbl)


def migrate_schema() -> None:
    """Apply lightweight upgrades create_all cannot do.

    - SQLite: PRAGMA-based ALTER + legacy FK rebuild
    - PostgreSQL/MySQL/other: inspector-based ADD COLUMN IF NOT EXISTS
    """
    url = str(engine.url)
    if url.startswith("sqlite"):
        with engine.begin() as conn:
            tables = {
                str(r[0])
                for r in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "accounts" in tables:
                for col, col_type in (
                    ("last_sync_at", "DATETIME"),
                    ("last_sync_error", "VARCHAR(512)"),
                    ("sync_enabled", "BOOLEAN DEFAULT 0"),
                    ("proxy", "VARCHAR(512)"),
                    ("latest_verification_code", "VARCHAR(64)"),
                    ("latest_code_at", "DATETIME"),
                    ("last_fetch_at", "DATETIME"),
                    ("last_error", "VARCHAR(512)"),
                ):
                    _sqlite_add_column(conn, "accounts", col, col_type)

            # Drop user system (no longer used)
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for tbl in (
                "user_sessions",
                "admin_sessions",
                "mail_index",
                "users",
            ):
                if tbl in tables:
                    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl}")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

            # After users dropped, rebuild accounts if legacy FK still points at users
            tables = {
                str(r[0])
                for r in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "accounts" in tables and _sqlite_accounts_has_users_fk(conn):
                _sqlite_rebuild_accounts_drop_users_fk(conn)
        return

    # Non-SQLite
    try:
        with engine.begin() as conn:
            _generic_add_missing_columns(conn)
    except Exception:
        logger.exception("migrate_schema: non-sqlite migration failed")


def init_db() -> None:
    """Create tables. Import models so metadata is populated."""
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_schema()
