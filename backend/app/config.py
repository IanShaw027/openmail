"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openmail_master_key: str = Field(
        default="",
        validation_alias="OPENMAIL_MASTER_KEY",
        description="AES-256 key: base64 (32 bytes) or 64 hex chars",
    )
    openmail_master_key_fallbacks: str = Field(
        default="",
        validation_alias="OPENMAIL_MASTER_KEY_FALLBACKS",
        description=(
            "Optional previous OPENMAIL_MASTER_KEY values (comma or newline separated). "
            "Used only for decrypt after key rotation; encrypt always uses primary."
        ),
    )
    openmail_database_url: str = Field(
        default="sqlite:///./openmail.db",
        validation_alias="OPENMAIL_DATABASE_URL",
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
        validation_alias="CORS_ORIGINS",
    )
    session_cookie_name: str = Field(
        default="openmail_session",
        validation_alias="SESSION_COOKIE_NAME",
    )
    admin_cookie_name: str = Field(
        default="openmail_admin",
        validation_alias="ADMIN_COOKIE_NAME",
    )
    session_max_age_seconds: int = Field(
        default=604800,
        validation_alias="SESSION_MAX_AGE_SECONDS",
    )
    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    cookie_samesite: str = Field(default="lax", validation_alias="COOKIE_SAMESITE")
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="PUBLIC_BASE_URL",
    )
    fetch_min_interval_seconds: float = Field(
        default=3.0,
        validation_alias="FETCH_MIN_INTERVAL_SECONDS",
        description="Engineering floor: min gap between real upstream fetches per account",
    )
    fetch_lock_lease_seconds: float = Field(
        default=180.0,
        validation_alias="FETCH_LOCK_LEASE_SECONDS",
        description="Max age of in_flight fetch lock before it is treated as stale (crash recovery)",
    )
    code_api_cache_ttl_seconds: float = Field(
        default=90.0,
        validation_alias="CODE_API_CACHE_TTL_SECONDS",
        description="TTL for public code-API short-circuit on latest_verification_code; 0 disables cache",
    )
    # Background sync worker
    sync_interval_seconds: int = Field(
        default=3600,
        validation_alias="SYNC_INTERVAL_SECONDS",
        description="Seconds between scheduled full sync cycles",
    )
    sync_concurrency: int = Field(
        default=2,
        validation_alias="SYNC_CONCURRENCY",
        description="Max concurrent account syncs per cycle",
    )
    sync_enabled_global: bool = Field(
        default=True,
        validation_alias="SYNC_ENABLED_GLOBAL",
        description="Master switch for the background sync worker",
    )
    proxy_template: str = Field(
        default="",
        validation_alias="PROXY_TEMPLATE",
        description="Single proxy URL template with {sid} (legacy; prefer PROXY_POOL)",
    )
    proxy_pool: str = Field(
        default="",
        validation_alias="PROXY_POOL",
        description="Multi-line proxy channels (CF Worker / socks / http). One URL per line; optional {sid}",
    )
    proxy_sid_strategy: str = Field(
        default="sticky_per_account",
        validation_alias="PROXY_SID_STRATEGY",
        description="sticky_per_account | rotate_per_sync | rotate_on_error | round_robin",
    )
    fetch_concurrency: int = Field(
        default=5,
        validation_alias="FETCH_CONCURRENCY",
        description="Suggested concurrent fetches for batch/import (clients may also limit)",
    )
    sync_folders: str = Field(
        default="inbox,junk",
        validation_alias="SYNC_FOLDERS",
        description="Comma-separated folders to sync per account",
    )

    # ── Quotas (no user accounts; device/license based) ──────────────
    quota_max_local_accounts: int = Field(
        default=100,
        validation_alias="QUOTA_MAX_LOCAL_ACCOUNTS",
        description="Max mailboxes in browser for unlicensed clients",
    )
    quota_max_cloud_accounts: int = Field(
        default=50,
        validation_alias="QUOTA_MAX_CLOUD_ACCOUNTS",
        description="Max server-stored credentials for unlicensed clients",
    )
    quota_max_poll_per_hour: int = Field(
        default=1000,
        validation_alias="QUOTA_MAX_POLL_PER_HOUR",
        description="Max upstream fetches per device per hour (unlicensed)",
    )
    code_api_max_fetch_per_hour: int = Field(
        default=60,
        validation_alias="CODE_API_MAX_FETCH_PER_HOUR",
        description=(
            "Max public code-API fetches per token per hour (abuse control); "
            "0 disables the limit. Also caps unknown-token requests per client IP."
        ),
    )
    code_api_max_refresh_per_hour: int = Field(
        default=15,
        validation_alias="CODE_API_MAX_REFRESH_PER_HOUR",
        description=(
            "Stricter cap for code-API refresh=1 (force upstream) per token per hour; "
            "0 disables the limit"
        ),
    )
    # Comma-separated pre-issued license tokens (unlimited quota when presented)
    license_tokens: str = Field(
        default="",
        validation_alias="LICENSE_TOKENS",
        description="Comma-separated tokens; clients send X-License-Token",
    )
    # HMAC secret: license can be HMAC-SHA256(device_fp, secret) hex
    license_hmac_secret: str = Field(
        default="",
        validation_alias="LICENSE_HMAC_SECRET",
        description="If set, X-License-Token may be HMAC hex of X-Device-Id",
    )
    # Incremental fetch defaults
    fetch_default_lookback_days: int = Field(
        default=3,
        validation_alias="FETCH_DEFAULT_LOOKBACK_DAYS",
        description="After first full fetch, only pull messages newer than N days",
    )
    mail_retention_days: int = Field(
        default=90,
        validation_alias="MAIL_RETENTION_DAYS",
        description="Default retention for indexed mail (client may override)",
    )
    # Soft-disable registration UI; API may still exist for migration
    auth_ui_enabled: bool = Field(
        default=False,
        validation_alias="AUTH_UI_ENABLED",
        description="Show login/register/admin in frontend when true",
    )

    @field_validator("cookie_samesite")
    @classmethod
    def _normalize_samesite(cls, v: str) -> str:
        allowed = {"lax", "strict", "none"}
        low = (v or "lax").lower()
        if low not in allowed:
            raise ValueError(f"COOKIE_SAMESITE must be one of {allowed}")
        return low

    @field_validator("proxy_sid_strategy")
    @classmethod
    def _normalize_sid_strategy(cls, v: str) -> str:
        allowed = {
            "sticky_per_account",
            "rotate_per_sync",
            "rotate_on_error",
            "round_robin",
        }
        low = (v or "sticky_per_account").strip().lower()
        if low not in allowed:
            raise ValueError(f"PROXY_SID_STRATEGY must be one of {allowed}")
        return low

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return self.openmail_database_url

    @property
    def sync_folder_list(self) -> list[str]:
        return [f.strip() for f in self.sync_folders.split(",") if f.strip()]

    @property
    def license_token_set(self) -> set[str]:
        return {t.strip() for t in self.license_tokens.split(",") if t.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
