"""Effective admin device ids: env recovery > data file > first trusted bootstrap."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from app.config import get_settings

logger = logging.getLogger("openmail.admin_ids")

_lock = Lock()
_ADMIN_ENV_KEY = "OPENMAIL_ADMIN_DEVICE_IDS"

try:
    import fcntl
except ImportError:  # Windows / non-POSIX
    fcntl = None  # type: ignore[assignment]


def parse_admin_ids(raw: str | None) -> set[str]:
    text = (raw or "").replace("\n", ",")
    return {t.strip() for t in text.split(",") if t.strip()}


def _env_admin_ids() -> set[str]:
    return parse_admin_ids(os.environ.get(_ADMIN_ENV_KEY, ""))


def _settings_admin_ids() -> set[str]:
    try:
        return set(get_settings().admin_device_id_set)
    except Exception:
        return set()


def _admin_ids_path() -> Path:
    data = os.environ.get("OPENMAIL_DATA_DIR", "").strip()
    if data:
        return Path(data) / "admin_device_ids"
    env_reg = os.environ.get("OPENMAIL_DEVICE_REGISTRY_PATH", "").strip()
    if env_reg:
        return Path(env_reg).parent / "admin_device_ids"
    try:
        db = (get_settings().openmail_database_url or "").replace("sqlite:///", "")
    except Exception:
        db = ""
    if db.startswith("/") and "openmail" in db:
        return Path(db).parent / "admin_device_ids"
    return Path("data") / "admin_device_ids"


def _read_file_ids(path: Path | None = None) -> set[str]:
    p = path or _admin_ids_path()
    try:
        if not p.is_file():
            return set()
        return parse_admin_ids(p.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("could not read admin ids file %s", p, exc_info=True)
        return set()


def effective_admin_ids() -> set[str]:
    """Non-empty env wins (operator recovery); else settings/.env; else data file."""
    env_ids = _env_admin_ids()
    if env_ids:
        return env_ids
    settings_ids = _settings_admin_ids()
    if settings_ids:
        return settings_ids
    return _read_file_ids()


def _persist_file(ids: set[str]) -> None:
    path = _admin_ids_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sorted(ids)) + ("\n" if ids else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _upsert_env_assignment(text: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    found = False
    for line in lines:
        stripped = line.lstrip("\ufeff").strip()
        if stripped.startswith(f"{_ADMIN_ENV_KEY}=") or stripped.startswith(f"#{_ADMIN_ENV_KEY}="):
            if not found:
                nl = "\n"
                if line.endswith("\r\n"):
                    nl = "\r\n"
                elif line.endswith("\n"):
                    nl = "\n"
                out.append(f"{_ADMIN_ENV_KEY}={value}{nl}")
                found = True
            continue
        out.append(line)
    body = "".join(out)
    if not found:
        if body and not body.endswith("\n"):
            body += "\n"
        body += f"{_ADMIN_ENV_KEY}={value}\n"
    return body


def _env_file_targets() -> list[Path]:
    targets: list[Path] = []
    explicit = os.environ.get("OPENMAIL_ENV_FILE", "").strip()
    if explicit:
        targets.append(Path(explicit))
        return targets
    # Tests must not rewrite the developer's project .env.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []
    cwd_env = Path(".env")
    if cwd_env.is_file():
        targets.append(cwd_env)
    return targets


def _try_write_dotenv(device_id: str) -> None:
    for path in _env_file_targets():
        try:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(_upsert_env_assignment(existing, device_id), encoding="utf-8")
            logger.info("wrote %s to %s", _ADMIN_ENV_KEY, path)
        except OSError:
            logger.info("could not persist admin id to %s", path, exc_info=True)


def maybe_bootstrap_admin(device_id: str, *, trusted: bool) -> bool:
    """If no admin is configured, the first trusted device becomes admin.

    Hot-reloads by setting OPENMAIL_ADMIN_DEVICE_IDS in-process and clearing
    the settings cache. Persists to data/admin_device_ids always; .env when
    OPENMAIL_ENV_FILE is set or a writable cwd .env exists (not during pytest).
    """
    pid = (device_id or "").strip()
    if not trusted or not pid.startswith("vk_"):
        return False
    with _lock:
        if effective_admin_ids():
            return False
        path = _admin_ids_path()
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                if effective_admin_ids():
                    return False
                _persist_file({pid})
                _try_write_dotenv(pid)
                os.environ[_ADMIN_ENV_KEY] = pid
                get_settings.cache_clear()
                logger.info("bootstrapped admin device %s", pid[:16])
                return True
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
