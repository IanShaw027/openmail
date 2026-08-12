"""Database engine, session factory, and base model."""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import bindparam, create_engine, event, inspect, text
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
        # WAL only applies to on-disk databases; in-memory test DBs stay default.
        is_memory = url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                # Matches pysqlite's own default timeout of 5s rather than
                # changing behaviour — kept explicit so the wait is visible here
                # and survives a driver whose default differs.
                cursor.execute("PRAGMA busy_timeout=5000")
                if not is_memory:
                    # WAL lets readers proceed during a write → fewer "database is
                    # locked" errors with the API + SyncWorker + fetch lease writing
                    # concurrently. NORMAL sync is the recommended WAL companion.
                    #
                    # Switching to WAL is itself a write and needs a writable
                    # *directory* for the -wal/-shm sidecars. If that fails, keep
                    # the connection on the rollback journal rather than turning a
                    # permissions problem into an unbootable app.
                    try:
                        cursor.execute("PRAGMA journal_mode=WAL")
                        cursor.execute("PRAGMA synchronous=NORMAL")
                    except Exception as exc:  # pragma: no cover - environment-specific
                        logger.warning(
                            "could not enable WAL (%s); falling back to the default "
                            "journal. Check that the database directory is writable.",
                            exc,
                        )
            finally:
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


def _ensure_account_owner_email_unique(conn) -> None:  # type: ignore[no-untyped-def]
    """Enforce cloud upsert identity without deleting ambiguous legacy duplicates."""
    try:
        indexes = {idx["name"] for idx in inspect(conn).get_indexes("accounts")}
        constraints = {
            item["name"] for item in inspect(conn).get_unique_constraints("accounts")
        }
        if "uq_accounts_owner_email" in indexes | constraints:
            return
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_accounts_owner_email "
                "ON accounts (owner_user_id, email)"
            )
        )
    except Exception:
        # Do not silently pick a winner among rows that may hold different secrets.
        # Log conflicting keys so operators can resolve before retrying migrate.
        try:
            rows = conn.execute(
                text(
                    "SELECT owner_user_id, email, COUNT(*) AS n "
                    "FROM accounts "
                    "GROUP BY owner_user_id, email "
                    "HAVING COUNT(*) > 1 "
                    "LIMIT 50"
                )
            ).fetchall()
            if rows:
                sample = [
                    f"{r[0]!s}:{r[1]!s}(x{r[2]})" for r in rows[:20]
                ]
                logger.error(
                    "migrate: duplicate (owner_user_id, email) pairs (sample): %s",
                    "; ".join(sample),
                )
                logger.error(
                    "migrate: resolve with e.g. "
                    "SELECT id, owner_user_id, email, created_at FROM accounts "
                    "WHERE owner_user_id = ? AND email = ? ORDER BY created_at; "
                    "then delete or reassign losers and re-run migrate."
                )
        except Exception:
            logger.debug("migrate: could not list duplicate account keys", exc_info=True)
        logger.exception(
            "migrate: cannot enforce unique account owner/email; resolve legacy duplicates"
        )
        raise RuntimeError(
            "cannot enforce unique account owner/email; resolve legacy duplicates "
            "(see logs for conflicting owner_user_id/email pairs)"
        )


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
    """Recreate accounts without FK to users so device_id can be stored in owner_user_id.

    Assumes the caller already disabled foreign keys: toggling them here would be
    a no-op, because this runs inside the migration transaction.
    """
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS accounts_new (
            id VARCHAR(40) NOT NULL PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            pool VARCHAR(32) NOT NULL,
            owner_user_id VARCHAR(128),
            password_enc TEXT,
            credential_enc TEXT,
            tag VARCHAR(128),
            note TEXT,
            status VARCHAR(32) NOT NULL,
            last_fetch_at DATETIME,
            last_error VARCHAR(512),
            latest_verification_code VARCHAR(64),
            latest_code_at DATETIME,
            latest_code_folder VARCHAR(32),
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


# Columns that may be missing on upgraded installs (create_all does not ALTER).
# dialect-agnostic ADD COLUMN statements — nullable / with default where needed.
_ACCOUNTS_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("owner_user_id", "VARCHAR(128)"),
    ("last_sync_at", "TIMESTAMP"),
    ("last_sync_error", "VARCHAR(512)"),
    ("sync_enabled", "BOOLEAN DEFAULT FALSE"),
    ("proxy", "VARCHAR(512)"),
    ("latest_verification_code", "VARCHAR(64)"),
    ("latest_code_at", "TIMESTAMP"),
    ("latest_code_folder", "VARCHAR(32)"),
    ("last_fetch_at", "TIMESTAMP"),
    ("last_error", "VARCHAR(512)"),
]


def _generic_add_missing_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """For PostgreSQL/MySQL/etc.: ADD COLUMN IF NOT EXISTS style via inspector."""
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "accounts" not in tables:
        return
    account_columns = insp.get_columns("accounts")
    existing = {c["name"] for c in account_columns}
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

    try:
        owner_col = next(c for c in account_columns if c["name"] == "owner_user_id")
        owner_type = owner_col.get("type")
        owner_len = getattr(owner_type, "length", None)
        if owner_len is None or owner_len < 128:
            if dialect == "postgresql":
                conn.execute(
                    text("ALTER TABLE accounts ALTER COLUMN owner_user_id TYPE VARCHAR(128)")
                )
            elif dialect in ("mysql", "mariadb"):
                conn.execute(text("ALTER TABLE accounts MODIFY owner_user_id VARCHAR(128)"))
    except StopIteration:
        pass
    except Exception:
        logger.exception("migrate: failed to widen accounts.owner_user_id")

    # Drop legacy user tables if present (local-first no longer uses them)
    for tbl in ("user_sessions", "admin_sessions", "mail_index", "users"):
        if tbl in tables:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
                logger.info("migrate: dropped legacy table %s", tbl)
            except Exception:
                logger.exception("migrate: failed to drop %s", tbl)

    if "fetch_lock_state" in tables:
        lock_columns = {c["name"] for c in insp.get_columns("fetch_lock_state")}
        if "lease_token" not in lock_columns:
            try:
                if dialect in ("postgresql", "mysql", "mariadb"):
                    conn.execute(
                        text(
                            "ALTER TABLE fetch_lock_state "
                            "ADD COLUMN IF NOT EXISTS lease_token VARCHAR(36)"
                        )
                    )
                else:
                    conn.execute(
                        text("ALTER TABLE fetch_lock_state ADD COLUMN lease_token VARCHAR(36)")
                    )
            except Exception:
                logger.exception("migrate: failed to add fetch_lock_state.lease_token")


# Runtime overrides for settings that no longer exist. `admin_password` is the
# reason this list exists: dropping it from OVERRIDABLE_KEYS stopped anything
# reading the row, which left a credential sitting in app_settings as plaintext
# JSON that no code path would ever touch again.
_DEAD_SETTING_KEYS = (
    "admin_password",
    "session_cookie_name",
    "admin_cookie_name",
    "session_max_age_seconds",
    "cookie_secure",
    "cookie_samesite",
)


def _purge_dead_settings_overrides(conn, tables: set[str]) -> None:  # type: ignore[no-untyped-def]
    if "app_settings" not in tables:
        return
    try:
        conn.execute(
            text("DELETE FROM app_settings WHERE key IN :keys").bindparams(
                bindparam("keys", expanding=True)
            ),
            {"keys": list(_DEAD_SETTING_KEYS)},
        )
    except Exception:
        logger.debug("migrate: could not purge dead app_settings overrides", exc_info=True)


def _sqlite_migrate(conn) -> None:  # type: ignore[no-untyped-def]
    """SQLite migration body. Runs inside a transaction with FKs already off."""
    tables = {
        str(r[0])
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "accounts" in tables:
        for col, col_type in (
            ("owner_user_id", "VARCHAR(128)"),
            ("last_sync_at", "DATETIME"),
            ("last_sync_error", "VARCHAR(512)"),
            ("sync_enabled", "BOOLEAN DEFAULT 0"),
            ("proxy", "VARCHAR(512)"),
            ("latest_verification_code", "VARCHAR(64)"),
            ("latest_code_at", "DATETIME"),
            ("latest_code_folder", "VARCHAR(32)"),
            ("last_fetch_at", "DATETIME"),
            ("last_error", "VARCHAR(512)"),
        ):
            _sqlite_add_column(conn, "accounts", col, col_type)
    if "fetch_lock_state" in tables:
        _sqlite_add_column(conn, "fetch_lock_state", "lease_token", "VARCHAR(36)")

    # Drop user system (no longer used)
    for tbl in ("user_sessions", "admin_sessions", "mail_index", "users"):
        if tbl in tables:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl}")

    _purge_dead_settings_overrides(conn, tables)

    # After users dropped, rebuild accounts if legacy FK still points at users
    tables = {
        str(r[0])
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "accounts" in tables and _sqlite_accounts_has_users_fk(conn):
        _sqlite_rebuild_accounts_drop_users_fk(conn)
    if "accounts" in tables:
        _ensure_account_owner_email_unique(conn)


def migrate_schema() -> None:
    """Apply lightweight upgrades create_all cannot do.

    - SQLite: PRAGMA-based ALTER + legacy FK rebuild
    - PostgreSQL/MySQL/other: inspector-based ADD COLUMN IF NOT EXISTS
    """
    url = str(engine.url)
    if url.startswith("sqlite"):
        # Foreign keys must be disabled for the legacy table drops and the
        # accounts rebuild. `PRAGMA foreign_keys` is a silent no-op inside a
        # transaction, so it has to be issued before the migration's BEGIN —
        # hence AUTOCOMMIT plus an explicit transaction, rather than
        # engine.begin(), which would open one first and make the pragma do
        # nothing. The explicit BEGIN/COMMIT keeps the rebuild atomic.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                conn.exec_driver_sql("BEGIN")
                try:
                    _sqlite_migrate(conn)
                    conn.exec_driver_sql("COMMIT")
                except Exception:
                    conn.exec_driver_sql("ROLLBACK")
                    raise
            finally:
                # Restore before the connection goes back to the pool.
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        return

    # Non-SQLite
    try:
        with engine.begin() as conn:
            _generic_add_missing_columns(conn)
            _ensure_account_owner_email_unique(conn)
            _purge_dead_settings_overrides(conn, set(inspect(conn).get_table_names()))
    except Exception:
        logger.exception("migrate_schema: non-sqlite migration failed")
        raise


def init_db() -> None:
    """Create tables. Import models so metadata is populated."""
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_schema()
