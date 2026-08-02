"""Mail providers (oauth, cookie, imap, http_api)."""

from app.providers.base import (
    CredentialUpdates,
    FetchResult,
    HealthResult,
    Message,
    Provider,
    ProviderNotImplemented,
    resolve_provider,
)

__all__ = [
    "CredentialUpdates",
    "FetchResult",
    "HealthResult",
    "Message",
    "Provider",
    "ProviderNotImplemented",
    "resolve_provider",
]
