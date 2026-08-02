"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AccountPool, AccountStatus, ProviderType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Accounts ──────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    provider: ProviderType = ProviderType.unknown
    pool: AccountPool | None = None
    password: str | None = None
    credential: dict[str, Any] | None = None
    tag: str | None = None
    note: str | None = None
    proxy: str | None = None
    sync_enabled: bool = False
    cookies: list[dict[str, Any]] | None = None
    # Client-side sealed blob (vault AES). Server cannot decrypt; admin cannot read plaintext.
    client_sealed: str | None = Field(
        default=None,
        max_length=2_000_000,
        description="Base64 JSON CipherPackage from browser vault",
    )


class AccountUpdate(BaseModel):
    email: str | None = None
    provider: ProviderType | None = None
    password: str | None = None
    credential: dict[str, Any] | None = None
    tag: str | None = None
    note: str | None = None
    proxy: str | None = None
    sync_enabled: bool | None = None
    status: AccountStatus | None = None
    cookies: list[dict[str, Any]] | None = None
    client_sealed: str | None = Field(default=None, max_length=2_000_000)


class AccountOut(ORMModel):
    id: str
    email: str
    provider: ProviderType
    pool: AccountPool
    owner_user_id: str | None
    tag: str | None
    note: str | None
    status: AccountStatus
    last_fetch_at: datetime | None
    last_error: str | None
    latest_verification_code: str | None
    latest_code_at: datetime | None
    sync_enabled: bool
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None
    proxy: str | None
    has_password: bool = False
    has_credential: bool = False
    has_session: bool = False
    client_sealed: bool = False
    created_at: datetime
    updated_at: datetime


# ── Code API ──────────────────────────────────────────────────────────


class CodeApiOut(BaseModel):
    ok: bool = True
    url: str
    token: str
    account_id: str
    created_at: datetime
    rotated_at: datetime | None = None
    enabled: bool = True


class CodeApiRotateRequest(BaseModel):
    current_token: str | None = None


class CodeFetchParams(BaseModel):
    format: Literal["text", "json", "json_compat", "nullx"] = "json"
    folder: str = "inbox"
    keyword: str | None = None
    regex: str | None = None
    refresh: int = 0
    quick: int = 1


class CodeFetchJsonResult(BaseModel):
    ok: bool
    code: str | None = None
    email: str | None = None
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    date: str | None = None
    folder: str | None = None
    fetched_at: str | None = None
    cached: bool | None = None
    error: str | None = None
    message: str | None = None

    model_config = ConfigDict(populate_by_name=True)


# ── Fetch ─────────────────────────────────────────────────────────────


class AccountFetchRequest(BaseModel):
    folder: str = "inbox"
    quick: bool = True
    force: bool = False


class ProxyFetchRequest(BaseModel):
    """Guest/proxy fetch — credentials travel in the body and are never stored."""

    email: str = Field(min_length=3, max_length=255)
    provider: ProviderType | None = None
    password: str | None = None
    credential: dict[str, Any] | None = None
    cookies: list[dict[str, Any]] | None = None
    folder: str = "inbox"
    quick: bool = True
    keyword: str | None = None
    regex: str | None = None
    # Optional fixed proxy for this mailbox (overrides admin pool)
    proxy: str | None = None
    # ISO datetime: only messages after this (incremental)
    since: str | None = None
    # ISO datetime: only messages strictly before this (load older / pagination)
    before: str | None = None
    # Cap how many messages to return (default provider quick ~15–20)
    max_messages: int | None = Field(default=None, ge=1, le=100)
    # Force full recent window (ignore since)
    full: bool = False


class SendMailRequest(BaseModel):
    """Send mail — only for local/private credentials, not public pool abuse."""

    to: list[str] = Field(min_length=1)
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    # Proxy/guest path (credentials not stored)
    email: str | None = None
    provider: ProviderType | str | None = None
    password: str | None = None
    credential: dict[str, Any] | None = None


class SendMailResponse(BaseModel):
    ok: bool
    error: str | None = None
    detail: str | None = None


class FetchMessageOut(BaseModel):
    id: str
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    from_address: str | None = None
    to: str | None = None
    date: str | None = None
    body_preview: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    verification_code: str | None = None
    folder: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class FetchResponse(BaseModel):
    ok: bool
    messages: list[FetchMessageOut] = Field(default_factory=list)
    message_count: int = 0
    folder: str = "inbox"
    fetched_at: datetime | None = None
    code: str | None = None
    cached: bool = False
    error: str | None = None
    email: str | None = None
    account_id: str | None = None
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    date: str | None = None
    retry_after: float | None = None
    # Rolling mail.com / cookie session for local-first clients
    session_cookies: list[dict[str, Any]] | None = None
    session_meta: dict[str, Any] | None = None
    session_restored: bool = False
    # HttpApi multi-inbox: temp addresses under one Worker / api_url
    mailboxes: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)


# ── Health ────────────────────────────────────────────────────────────


class HealthOut(BaseModel):
    ok: bool
    service: str = "openmail"
    version: str
    master_key_configured: bool
