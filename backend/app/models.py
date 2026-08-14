"""SQLAlchemy ORM models for OpenMail (local-first; no user/admin tables)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str = "") -> str:
    uid = uuid.uuid4().hex
    return f"{prefix}{uid}" if prefix else uid


class AccountPool(str, enum.Enum):
    """Legacy pool labels kept for DB compatibility; app is browser-local."""

    public = "public"
    user_private = "user_private"


class ProviderType(str, enum.Enum):
    oauth = "oauth"
    cookie = "cookie"
    imap = "imap"
    http_api = "http_api"
    unknown = "unknown"


class AccountStatus(str, enum.Enum):
    ok = "ok"
    error = "error"
    need_reauth = "need_reauth"
    disabled = "disabled"


class Account(Base):
    """Optional server-side credential row (legacy code-api tokens).

    Primary product path stores credentials only in the browser.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "email", name="uq_accounts_owner_email"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("acc_"))
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[ProviderType] = mapped_column(
        Enum(ProviderType), default=ProviderType.unknown, nullable=False
    )
    pool: Mapped[AccountPool] = mapped_column(
        Enum(AccountPool), default=AccountPool.public, nullable=False, index=True
    )
    # No FK to users — column kept nullable for old rows until migrate drops it
    owner_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus), default=AccountStatus.ok, nullable=False
    )
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latest_verification_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_code_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_code_folder: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proxy: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    session: Mapped[AccountSession | None] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )
    code_api_token: Mapped[CodeApiToken | None] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("acs_"))
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    cookies_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    absolute_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    account: Mapped[Account] = relationship(back_populates="session")


class CodeApiToken(Base):
    __tablename__ = "code_api_tokens"
    __table_args__ = (UniqueConstraint("account_id", name="uq_code_api_account"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("cat_"))
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_format: Mapped[str] = mapped_column(String(32), default="json", nullable=False)
    default_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_regex: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="code_api_token")


class FetchLockState(Base):
    __tablename__ = "fetch_lock_state"

    account_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_real_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    in_flight: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DevicePollEvent(Base):
    """Durable poll/fetch/send events for per-device hourly quota (multi-worker safe)."""

    __tablename__ = "device_poll_events"
    __table_args__ = (
        Index("ix_device_poll_events_device_ts", "device_id", "ts"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("pol_"))
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )


class DevicePollQuotaState(Base):
    """Per-device lock row used to serialize hourly poll quota checks."""

    __tablename__ = "device_poll_quota_state"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DeviceAuthReplay(Base):
    """Durable HMAC replay keys so multi-worker processes share the window."""

    __tablename__ = "device_auth_replays"

    replay_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class DeviceQuotaState(Base):
    """Durable per-device account quota counter."""

    __tablename__ = "device_quota_state"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cloud_accounts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("syn_"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)


class SyncCursor(Base):
    """Per account×folder water-mark for server-side poll (time/uid/delta)."""

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("account_id", "folder", name="uq_sync_cursors_account_folder"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("cur_"))
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="time", nullable=False)
    cursor_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MailItem(Base):
    """Server-stored mail for cloud poll + device delta sync."""

    __tablename__ = "mail_items"
    __table_args__ = (
        UniqueConstraint("account_id", "folder", "stable_id", name="uq_mail_items_account_folder_sid"),
        Index("ix_mail_items_account_updated", "account_id", "updated_at"),
        Index("ix_mail_items_account_folder_received", "account_id", "folder", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("mid_"))
    account_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder: Mapped[str] = mapped_column(String(32), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_addr: Mapped[str | None] = mapped_column(String(512), nullable=True)
    to_addrs: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LicenseCode(Base):
    """Admin-issued quota-unlock code. Plaintext is encrypted at rest."""

    __tablename__ = "license_codes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("lic_"))
    token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LicenseCodeUse(Base):
    """HMAC-proven device that presented an issued license code."""

    __tablename__ = "license_code_uses"
    __table_args__ = (
        UniqueConstraint("token_hash", "device_id", name="uq_license_code_uses_hash_device"),
        Index("ix_license_code_uses_token_hash", "token_hash"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _new_id("lcu_"))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
