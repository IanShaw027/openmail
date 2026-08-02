"""Redact secrets from log / error strings."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|refresh_token|access_token|authorization|"
    r"cookie|api[_-]?key|client_secret|auth_code|credential)",
    re.I,
)

_LONG_TOKEN = re.compile(r"\b(M\.[A-Za-z0-9._\-!*]{40,}|[A-Za-z0-9+/]{40,}={0,2})\b")


def redact_text(text: str | None, *, max_len: int = 512) -> str:
    if not text:
        return ""
    s = str(text)
    # key=value style
    s = re.sub(
        rf"({_SENSITIVE_KEYS.pattern})\s*[=:]\s*([^\s,;\"']+)",
        r"\1=***",
        s,
        flags=re.I,
    )
    s = _LONG_TOKEN.sub("***", s)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def redact_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    out: dict[str, Any] = {}
    for k, v in data.items():
        if _SENSITIVE_KEYS.search(str(k)):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact_mapping(v)
        elif isinstance(v, str):
            out[k] = redact_text(v)
        else:
            out[k] = v
    return out
