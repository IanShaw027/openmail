"""Resolve per-account proxy URL.

All proxy routing is **in-process** inside OpenMail (no extra sidecar service).
You only configure outbound proxy URLs (e.g. your own Cloudflare Worker / residential
gateways) in admin settings; OpenMail picks a channel and attaches it to httpx/curl.

Priority:
1. account.proxy — **fixed proxy for this mailbox** (never rotated)
2. Multi-line proxy_pool (or legacy single proxy_template) with sticky / rotate
3. None (direct egress from this server)
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from typing import Any, Protocol

from app.services.settings_service import EffectiveSettings

_rr_lock = threading.Lock()
_rr_index = 0


class _AccountLike(Protocol):
    id: str | None
    proxy: str | None
    email: str | None


def sticky_sid(key: str) -> str:
    """Stable 16-hex sid derived from account id or email."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def new_sid() -> str:
    return uuid.uuid4().hex[:16]


def resolve_sid(
    account_key: str | None,
    *,
    strategy: str,
    force_new_sid: bool = False,
) -> str:
    strategy = (strategy or "sticky_per_account").strip().lower()
    if force_new_sid or strategy == "rotate_per_sync":
        return new_sid()
    if strategy == "rotate_on_error":
        if account_key:
            return sticky_sid(account_key)
        return new_sid()
    if account_key:
        return sticky_sid(account_key)
    return new_sid()


def apply_proxy_template(template: str, sid: str) -> str | None:
    tpl = (template or "").strip()
    if not tpl:
        return None
    return tpl.replace("{sid}", sid)


def parse_proxy_pool(settings: EffectiveSettings | Any) -> list[str]:
    """Return non-empty proxy channel templates.

    Sources (merged, de-duplicated, order preserved):
    - proxy_pool: multi-line, backslash-n escaped, or pipe-separated URLs
    - proxy_template: legacy single template when pool empty
    """
    lines: list[str] = []
    pool = str(getattr(settings, "proxy_pool", "") or "")
    # Docker Compose single-line env may use the two-char sequence \\n
    pool = pool.replace("\\n", "\n")
    # Pipe-separated list: socks5://a:1080|socks5://b:1080
    if "|" in pool and "\n" not in pool and pool.count("://") >= 2:
        pool = pool.replace("|", "\n")
    if pool.strip():
        for part in re.split(r"[\r\n]+", pool):
            p = part.strip()
            if not p or p.startswith("#"):
                continue
            lines.append(p)
    single = str(getattr(settings, "proxy_template", "") or "").strip()
    if single and not lines:
        lines.append(single)
    seen: set[str] = set()
    out: list[str] = []
    for x in lines:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out



def _account_key(account: _AccountLike | Any) -> str | None:
    aid = getattr(account, "id", None)
    if aid:
        return str(aid)
    email = getattr(account, "email", None)
    if email:
        return str(email).strip().lower()
    return None


def pick_pool_template(
    channels: list[str],
    *,
    account_key: str | None,
    strategy: str,
    force_new_sid: bool = False,
) -> str | None:
    """Pick one channel from the pool (sticky by account, or round-robin)."""
    if not channels:
        return None
    if len(channels) == 1:
        return channels[0]

    strategy = (strategy or "sticky_per_account").strip().lower()
    if force_new_sid or strategy in ("rotate_per_sync", "round_robin"):
        global _rr_index
        with _rr_lock:
            idx = _rr_index % len(channels)
            _rr_index += 1
        return channels[idx]

    # sticky: same account always same channel (good for cookie/session affinity)
    key = account_key or new_sid()
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    return channels[h % len(channels)]


def resolve_proxy(
    account: _AccountLike | Any,
    *,
    settings: EffectiveSettings | Any,
    force_new_sid: bool = False,
    db: Any = None,
) -> str | None:
    """
    Resolve proxy URL for an account / guest synthetic account.

    Priority:
    1. account.proxy — fixed per-mailbox override
    2. proxy_pool / proxy_template channel + {sid}
    3. None
    """
    _ = db
    candidates = list_proxy_candidates(
        account,
        settings=settings,
        force_new_sid=force_new_sid,
        include_direct=False,
    )
    return candidates[0] if candidates else None


def list_proxy_candidates(
    account: _AccountLike | Any,
    *,
    settings: EffectiveSettings | Any,
    force_new_sid: bool = False,
    include_direct: bool = True,
) -> list[str | None]:
    """Ordered egress list: sticky/fixed first, then remaining pool, then direct.

    Used when a channel fails soft (SSO/rate-limit): walk every WARP worker
    before falling back to the server's own IP (``None``).

    Fixed ``account.proxy`` is never rotated — only that URL is returned.
    """
    explicit = getattr(account, "proxy", None)
    if explicit is not None and str(explicit).strip():
        fixed = str(explicit).strip()
        return [fixed]

    channels = parse_proxy_pool(settings)
    if not channels:
        return [None] if include_direct else []

    strategy = getattr(settings, "proxy_sid_strategy", "sticky_per_account") or "sticky_per_account"
    key = _account_key(account)
    sid = resolve_sid(key, strategy=str(strategy), force_new_sid=force_new_sid)

    # Start at sticky index so happy path stays on the same worker
    global _rr_index
    if force_new_sid or str(strategy).strip().lower() in ("rotate_per_sync", "round_robin"):
        with _rr_lock:
            start = _rr_index % len(channels)
            # advance global rr so next account doesn't all pile on same start
            _rr_index += 1
    else:
        hkey = key or new_sid()
        start = int(hashlib.sha256(hkey.encode("utf-8")).hexdigest()[:8], 16) % len(channels)

    ordered: list[str] = []
    for i in range(len(channels)):
        tpl = channels[(start + i) % len(channels)]
        url = apply_proxy_template(tpl, sid)
        if url and url not in ordered:
            ordered.append(url)

    result: list[str | None] = list(ordered)
    if include_direct:
        result.append(None)
    return result


def resolve_proxy_for_email(
    email: str,
    *,
    settings: EffectiveSettings | Any,
    fixed_proxy: str | None = None,
    force_new_sid: bool = False,
) -> str | None:
    """Guest/local path: optional fixed proxy, else sticky pool by email."""
    if fixed_proxy and str(fixed_proxy).strip():
        return str(fixed_proxy).strip()
    account = type("A", (), {"id": None, "proxy": None, "email": email})()
    return resolve_proxy(account, settings=settings, force_new_sid=force_new_sid)
