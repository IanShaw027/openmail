"""Provider protocol and shared result types.

Concrete providers (oauth / cookie / imap / http_api) implement Provider.
OAuth (Graph) and HttpApi are real; cookie (mail.com) and IMAP are implemented
in sibling modules and registered here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class Message:
    id: str
    subject: str = ""
    from_: str = ""
    from_address: str = ""
    to: str = ""
    date: str | None = None
    body_preview: str = ""
    body_text: str = ""
    body_html: str = ""
    folder: str = "inbox"
    verification_code: str | None = None
    raw_refs: dict[str, Any] = field(default_factory=dict)
    # IMAP: UIDVALIDITY of the selected mailbox (UIDs only unique per validity)
    uidvalidity: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "from": self.from_,
            "from_address": self.from_address,
            "to": self.to,
            "date": self.date,
            "body_preview": self.body_preview,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "folder": self.folder,
            "verification_code": self.verification_code,
            "raw_refs": self.raw_refs,
        }
        if self.uidvalidity is not None:
            d["uidvalidity"] = self.uidvalidity
        return d


@dataclass
class CredentialUpdates:
    """Rolling credential write-backs after a successful fetch."""

    refresh_token: str | None = None
    access_token: str | None = None
    session_cookies: list[dict[str, Any]] | None = None
    session_meta: dict[str, Any] | None = None
    password: str | None = None
    # CF Worker / multi-inbox: discovered temp addresses under one api_url
    mailboxes: list[str] | None = None

    def any(self) -> bool:
        return any(
            [
                self.refresh_token,
                self.access_token,
                self.session_cookies is not None,
                self.session_meta is not None,
                self.password,
                self.mailboxes is not None,
            ]
        )


@dataclass
class FetchResult:
    ok: bool
    messages: list[Message] = field(default_factory=list)
    message_count: int = 0
    folder: str = "inbox"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    credential_updates: CredentialUpdates | None = None
    session_restored: bool = False
    error: str | None = None
    # IMAP SELECT UIDVALIDITY for the fetched folder (propagated to messages)
    uidvalidity: int | None = None
    phase: str = "full"
    pending_body_ids: list[str] = field(default_factory=list)
    partial: bool = False

    def __post_init__(self) -> None:
        if self.message_count == 0 and self.messages:
            self.message_count = len(self.messages)
        if self.uidvalidity is not None:
            for m in self.messages:
                if m.uidvalidity is None:
                    m.uidvalidity = self.uidvalidity


@dataclass
class HealthResult:
    ok: bool
    detail: str | None = None


class ProviderNotImplemented(Exception):
    """Provider fetch path not yet implemented."""


@runtime_checkable
class Provider(Protocol):
    name: str
    # "since_before" = honors limits.since / limits.before; "none" = max_messages only
    time_paging: str

    def can_handle(self, account: Any) -> bool:
        """Return True if this provider can process the account."""
        ...

    def fetch(
        self,
        account: Any,
        *,
        folder: str = "inbox",
        quick: bool = True,
        limits: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
    ) -> FetchResult:
        """Fetch messages for account. May raise ProviderNotImplemented."""
        ...

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        """Optional connectivity probe."""
        ...


def filter_messages_by_time(
    messages: list[Message],
    *,
    since: str | None = None,
    before: str | None = None,
) -> list[Message]:
    """Client-side date filter for providers that lack server-side since/before.

    Messages without a parseable date are kept when filtering by since (cannot
    prove they are old) and dropped when filtering by before if unparseable
    (cannot prove they are older).
    """
    if not since and not before:
        return messages

    def _ms(date: str | int | float | None) -> float | None:
        if date is None:
            return None
        if isinstance(date, (int, float)):
            value = float(date)
            return value if abs(value) >= 100_000_000_000 else value * 1000.0
        if not isinstance(date, str):
            return None
        s = date.strip()
        if not s:
            return None
        try:
            numeric = float(s)
            return numeric if abs(numeric) >= 100_000_000_000 else numeric * 1000.0
        except ValueError:
            pass
        # mail.com UI: "Tuesday, August 04, 2026 at 10:56 AM"
        s_norm = re.sub(r"\s+at\s+", " ", s, flags=re.I)
        s_norm = re.sub(r"\s+", " ", s_norm).strip()
        for candidate in (s_norm, s, re.sub(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+", "", s_norm, flags=re.I)):
            if not candidate:
                continue
            try:
                iso = candidate.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp() * 1000.0
            except Exception:
                pass
            try:
                dt = parsedate_to_datetime(candidate)
                if dt is None:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp() * 1000.0
            except Exception:
                pass
            # strptime for "Month DD, YYYY H:MM AM/PM" (mail.com after stripping "at")
            for fmt in (
                "%A, %B %d, %Y %I:%M %p",
                "%B %d, %Y %I:%M %p",
                "%A, %B %d, %Y %H:%M",
                "%B %d, %Y %H:%M",
            ):
                try:
                    dt = datetime.strptime(candidate, fmt).replace(tzinfo=timezone.utc)
                    return dt.timestamp() * 1000.0
                except ValueError:
                    continue
        return None

    since_ms = _ms(since) if since else None
    before_ms = _ms(before) if before else None
    # small slack so boundary messages are not dropped by clock skew
    if since_ms is not None:
        since_ms -= 120_000.0
    out: list[Message] = []
    for m in messages:
        t = _ms(m.date)
        if since_ms is not None:
            if t is None:
                out.append(m)
                continue
            if t < since_ms:
                continue
        if before_ms is not None:
            if t is None:
                continue
            if t >= before_ms:
                continue
        out.append(m)
    return out


class StubProvider:
    """Base stub used until real providers are implemented."""

    name: str = "stub"
    time_paging: str = "none"
    _user_error: str = "该取件方式尚未实现 / Provider not implemented yet"

    def can_handle(self, account: Any) -> bool:
        return False

    def fetch(
        self,
        account: Any,
        *,
        folder: str = "inbox",
        quick: bool = True,
        limits: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
    ) -> FetchResult:
        return FetchResult(ok=False, folder=folder, error=self._user_error)

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        return HealthResult(ok=False, detail=self._user_error)


def _build_registry() -> list[Provider]:
    """Lazy-import concrete providers so base stays light for type imports."""
    from app.providers.cookie_mailcom import MailcomCookieProvider
    from app.providers.http_api import HttpApiProvider
    from app.providers.imap_provider import ImapProvider
    from app.providers.oauth_graph import OAuthGraphProvider

    return [
        OAuthGraphProvider(),
        MailcomCookieProvider(),
        ImapProvider(),
        HttpApiProvider(),
    ]


_REGISTRY: list[Provider] | None = None


def get_registry() -> list[Provider]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def reset_registry_for_tests(registry: list[Provider] | None = None) -> None:
    """Test helper: replace or clear provider registry (None rebuilds default)."""
    global _REGISTRY
    _REGISTRY = registry


def resolve_provider(account: Any) -> Provider | None:
    for p in get_registry():
        if p.can_handle(account):
            return p
    return None


def __getattr__(name: str) -> Any:
    """Lazy re-exports for tests that import provider classes from base."""
    if name == "CookieProvider":
        from app.providers.cookie_mailcom import MailcomCookieProvider

        return MailcomCookieProvider
    if name == "ImapProvider":
        from app.providers.imap_provider import ImapProvider

        return ImapProvider
    if name == "OAuthProvider":
        from app.providers.oauth_graph import OAuthGraphProvider

        return OAuthGraphProvider
    if name == "HttpApiProvider":
        from app.providers.http_api import HttpApiProvider

        return HttpApiProvider
    if name == "reset_registry":
        return reset_registry_for_tests
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
