"""Effective settings: env defaults merged with DB app_settings overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AppSetting

# Keys that admins may override at runtime (stored as JSON strings in app_settings).
OVERRIDABLE_KEYS = frozenset(
    {
        "sync_interval_seconds",
        "sync_concurrency",
        "sync_enabled_global",
        "proxy_template",
        "proxy_pool",
        "proxy_sid_strategy",
        "sync_folders",
        "fetch_concurrency",
        "admin_password",
    }
)

_SID_STRATEGIES = frozenset(
    {
        "sticky_per_account",
        "rotate_per_sync",
        "rotate_on_error",
        "round_robin",
    }
)


@dataclass
class EffectiveSettings:
    """Runtime-effective sync/proxy settings (env + DB overrides)."""

    sync_interval_seconds: int
    sync_concurrency: int
    sync_enabled_global: bool
    proxy_template: str
    proxy_pool: str
    proxy_sid_strategy: str
    sync_folders: str
    fetch_concurrency: int = 5
    admin_password: str = ""
    # Passthrough of non-overridable env settings used by fetch
    fetch_min_interval_seconds: float = 3.0
    openmail_master_key: str = ""
    public_base_url: str = ""

    @property
    def sync_folder_list(self) -> list[str]:
        return [f.strip() for f in self.sync_folders.split(",") if f.strip()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sync_interval_seconds": self.sync_interval_seconds,
            "sync_concurrency": self.sync_concurrency,
            "sync_enabled_global": self.sync_enabled_global,
            "proxy_template": self.proxy_template,
            "proxy_pool": self.proxy_pool,
            "proxy_sid_strategy": self.proxy_sid_strategy,
            "sync_folders": self.sync_folders,
            "sync_folder_list": self.sync_folder_list,
            "fetch_concurrency": self.fetch_concurrency,
        }


def _coerce_value(key: str, raw: Any) -> Any:
    if key in ("sync_interval_seconds", "sync_concurrency"):
        return int(raw)
    if key == "sync_enabled_global":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if key == "proxy_sid_strategy":
        s = str(raw).strip().lower()
        if s not in _SID_STRATEGIES:
            raise ValueError(
                f"proxy_sid_strategy must be one of {sorted(_SID_STRATEGIES)}"
            )
        return s
    if key in ("proxy_template", "proxy_pool", "sync_folders", "admin_password"):
        return str(raw)
    if key == "fetch_concurrency":
        return int(raw)
    return raw


def _load_db_overrides(db: Session) -> dict[str, Any]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(OVERRIDABLE_KEYS)).all()
    out: dict[str, Any] = {}
    for row in rows:
        try:
            parsed = json.loads(row.value)
        except (TypeError, json.JSONDecodeError):
            parsed = row.value
        try:
            out[row.key] = _coerce_value(row.key, parsed)
        except (TypeError, ValueError):
            continue
    return out


def get_effective_settings(
    db: Session | None = None,
    *,
    settings: Settings | None = None,
) -> EffectiveSettings:
    """Merge env Settings with optional DB app_settings overrides."""
    base = settings or get_settings()
    overrides: dict[str, Any] = {}
    if db is not None:
        try:
            overrides = _load_db_overrides(db)
        except Exception:
            overrides = {}

    return EffectiveSettings(
        sync_interval_seconds=int(
            overrides.get("sync_interval_seconds", base.sync_interval_seconds)
        ),
        sync_concurrency=int(
            overrides.get("sync_concurrency", base.sync_concurrency)
        ),
        sync_enabled_global=bool(
            overrides.get("sync_enabled_global", base.sync_enabled_global)
        ),
        proxy_template=str(overrides.get("proxy_template", base.proxy_template) or ""),
        proxy_pool=str(
            overrides.get("proxy_pool", getattr(base, "proxy_pool", "") or "") or ""
        ),
        proxy_sid_strategy=str(
            overrides.get("proxy_sid_strategy", base.proxy_sid_strategy)
        ),
        sync_folders=str(overrides.get("sync_folders", base.sync_folders) or "inbox,junk"),
        fetch_concurrency=int(
            overrides.get(
                "fetch_concurrency",
                getattr(base, "fetch_concurrency", 5) or 5,
            )
        ),
        admin_password=str(
            overrides.get("admin_password", base.admin_password) or base.admin_password
        ),
        fetch_min_interval_seconds=float(base.fetch_min_interval_seconds),
        openmail_master_key=base.openmail_master_key,
        public_base_url=base.public_base_url,
    )


def set_overrides(
    db: Session,
    updates: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> EffectiveSettings:
    """Persist override keys; unknown keys rejected. Empty string / null clears override."""
    now = datetime.now(timezone.utc)
    for key, value in updates.items():
        if key not in OVERRIDABLE_KEYS:
            raise ValueError(f"unknown setting key: {key}")
        if value is None:
            existing = db.get(AppSetting, key)
            if existing is not None:
                db.delete(existing)
            continue
        coerced = _coerce_value(key, value)
        # Validate ranges
        if key == "sync_interval_seconds" and int(coerced) < 60:
            raise ValueError("sync_interval_seconds must be >= 60")
        if key == "sync_concurrency":
            c = int(coerced)
            if c < 1 or c > 32:
                raise ValueError("sync_concurrency must be 1..32")
        if key == "fetch_concurrency":
            c = int(coerced)
            if c < 1 or c > 32:
                raise ValueError("fetch_concurrency must be 1..32")
        if key == "admin_password":
            pw = str(coerced)
            if len(pw) < 8:
                raise ValueError("admin_password must be at least 8 characters")
        row = db.get(AppSetting, key)
        encoded = json.dumps(coerced)
        if row is None:
            db.add(AppSetting(key=key, value=encoded, updated_at=now))
        else:
            row.value = encoded
            row.updated_at = now
    db.commit()
    return get_effective_settings(db, settings=settings)


def as_settings_compatible(eff: EffectiveSettings, base: Settings | None = None) -> Settings:
    """Return a Settings-like object for callers that need Settings fields.

    Uses a shallow copy of env Settings with overridable fields patched.
    """
    s = base or get_settings()
    # pydantic models are immutable-ish; construct a new one from dump
    data = s.model_dump()
    data["sync_interval_seconds"] = eff.sync_interval_seconds
    data["sync_concurrency"] = eff.sync_concurrency
    data["sync_enabled_global"] = eff.sync_enabled_global
    data["proxy_template"] = eff.proxy_template
    if "proxy_pool" in data or hasattr(s, "proxy_pool"):
        data["proxy_pool"] = eff.proxy_pool
    else:
        data["proxy_pool"] = eff.proxy_pool
    data["proxy_sid_strategy"] = eff.proxy_sid_strategy
    data["sync_folders"] = eff.sync_folders
    if "fetch_concurrency" in data or hasattr(s, "fetch_concurrency"):
        data["fetch_concurrency"] = eff.fetch_concurrency
    else:
        data["fetch_concurrency"] = eff.fetch_concurrency
    data["admin_password"] = eff.admin_password or s.admin_password
    return Settings(**data)
