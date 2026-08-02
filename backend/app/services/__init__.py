"""Shared backend services (parser, credentials, fetch, ssrf)."""

from app.services.parser import (
    annotate_message_code,
    attach_verification_code,
    extract_code,
    extract_verification_code,
)

__all__ = [
    "annotate_message_code",
    "attach_verification_code",
    "extract_code",
    "extract_verification_code",
]
