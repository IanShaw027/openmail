"""Fetch orchestration: decrypt credentials, route provider, apply updates, cache codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import CryptoError, decrypt_str_or_plain, encrypt_str
from app.fetch_guard import (
    FetchInFlightError,
    FetchTooSoonError,
    account_fetch_slot,
    lease_is_current,
)
from app.models import Account, AccountStatus, ProviderType
from app.providers.base import (
    FetchResult,
    Message,
    ProviderNotImplemented,
    resolve_provider,
)
from app.services.credentials import (
    apply_credential_updates,
    load_cookies,
    load_credentials,
    load_password,
    load_session_meta,
    merge_guest_credentials,
)
from app.services.parser import annotate_message_code, extract_verification_code

# Interactive browser clients abort ~55–90s. Walking the entire 10-node WARP
# pool × multi-step mail.com login easily exceeds that (nginx 499).
# Cap: sticky + optional alternate + optional direct.
MAX_EGRESS_ATTEMPTS = 3
# Cookie / mail.com SSO is much heavier than IMAP — tighter egress budget.
MAX_EGRESS_ATTEMPTS_COOKIE = 2


def build_fetch_limits(
    *,
    since: str | None = None,
    before: str | None = None,
    full: bool = False,
    quick: bool = False,
    max_messages: int | None = None,
    default_quick: int = 15,
    default_page: int = 50,
) -> dict[str, Any]:
    """Build provider ``limits``. ``since`` stays set when paging with ``before``."""
    limits: dict[str, Any] = {}
    if since and not full:
        limits["since"] = since
    if before:
        limits["before"] = before
    if max_messages is not None:
        try:
            limits["max_messages"] = max(1, min(int(max_messages), 100))
        except (TypeError, ValueError):
            limits["max_messages"] = default_quick if quick else default_page
    elif quick:
        limits["max_messages"] = default_quick
    elif not full:
        limits["max_messages"] = default_page
    return limits


@dataclass
class FetchServiceResult:
    ok: bool
    messages: list[Message] = field(default_factory=list)
    message_count: int = 0
    folder: str = "inbox"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None
    code: str | None = None
    cached: bool = False
    account_id: str | None = None
    email: str | None = None
    too_soon: bool = False
    retry_after: float | None = None
    subject: str | None = None
    from_: str | None = None
    date: str | None = None
    # Rolling session for local-first clients (mail.com cookies etc.)
    session_cookies: list[dict[str, Any]] | None = None
    session_meta: dict[str, Any] | None = None
    session_restored: bool = False
    # HttpApi multi-inbox: temp addresses discovered under one api_url
    mailboxes: list[str] | None = None
    # IMAP mailbox UIDVALIDITY for this folder (optional)
    uidvalidity: int | None = None
    credential_updates: dict[str, str] | None = None
    phase: str = "full"
    pending_body_ids: list[str] = field(default_factory=list)
    partial: bool = False

    def __post_init__(self) -> None:
        if self.message_count == 0 and self.messages:
            self.message_count = len(self.messages)


def _provider_str(provider: Any) -> str:
    if provider is None:
        return "unknown"
    return str(getattr(provider, "value", provider))


def infer_provider(
    *,
    provider: Any = None,
    credentials: dict[str, Any] | None = None,
    password: str | None = None,
) -> str:
    """Infer provider type from explicit value or credential fields."""
    explicit = _provider_str(provider)
    if explicit and explicit not in ("unknown", ""):
        return explicit
    creds = credentials or {}
    if creds.get("client_id") and creds.get("refresh_token"):
        return "oauth"
    if creds.get("api_url"):
        return "http_api"
    if creds.get("imap_host") or creds.get("host"):
        return "imap"
    if password or creds.get("password") or creds.get("cookies") or creds.get("site"):
        return "cookie"
    return "unknown"


def _build_credentials_for_account(
    account: Account,
    *,
    settings: Settings,
    db: Session | None = None,
    force_new_sid: bool = False,
) -> dict[str, Any]:
    creds = load_credentials(account, settings=settings)
    pw = load_password(account, settings=settings)
    if pw and "password" not in creds:
        creds["password"] = pw
    cookies = load_cookies(account, settings=settings)
    if cookies is not None:
        creds["cookies"] = cookies
    # Cookie providers (mail.com) need session_meta for restore / CSRF / site state
    meta = load_session_meta(account, settings=settings)
    if meta is not None:
        creds["session_meta"] = meta
    # Ensure email available to providers
    creds.setdefault("email", account.email)

    # Proxy is attached by fetch_account / fetch_proxy via list_proxy_candidates
    # (rotation). Keep fixed account.proxy only as a hint for the candidate list.
    if account.proxy:
        creds.setdefault("_fixed_proxy", account.proxy)
    return creds


def _pick_best_code(
    messages: list[Message],
    *,
    keyword: str | None = None,
    custom_regex: str | None = None,
) -> tuple[str | None, Message | None]:
    """Return (code, message) preferring first matching message with a code."""
    filtered = messages
    if keyword:
        kw = keyword.lower()
        filtered = [
            m
            for m in messages
            if kw in (m.subject or "").lower()
            or kw in (m.body_text or "").lower()
            or kw in (m.body_preview or "").lower()
            or kw in (m.from_ or "").lower()
        ] or messages

    for m in filtered:
        code = m.verification_code
        if not code:
            code = annotate_message_code(m, custom_regex=custom_regex)
        if code:
            return code, m
    # Re-scan with custom regex only
    if custom_regex:
        for m in filtered:
            code = extract_verification_code(
                subject=m.subject,
                body_text=m.body_text,
                body_html=m.body_html,
                body_preview=m.body_preview,
                custom_regex=custom_regex,
            )
            if code:
                m.verification_code = code
                return code, m
    return None, filtered[0] if filtered else None


def _latest_code_plain(account: Account, settings: Settings | None = None) -> str | None:
    return decrypt_str_or_plain(account.latest_verification_code, settings=settings)


def _mark_fetch_ok(account: Account) -> None:
    now = datetime.now(timezone.utc)
    account.last_fetch_at = now
    account.last_error = None
    if account.status == AccountStatus.error:
        account.status = AccountStatus.ok
    account.updated_at = now


def _write_short_cache(
    db: Session,
    account: Account,
    messages: list[Message],
    code: str | None,
    *,
    matched: Message | None = None,
    folder: str = "inbox",
) -> None:
    _mark_fetch_ok(account)
    # Console / code-API "latest code" is inbox-only. Spam/sent OTPs stay on
    # the message rows but must not overwrite the account column.
    if code and _folder_key(folder) == "inbox":
        account.latest_verification_code = encrypt_str(code)
        account.latest_code_at = datetime.now(timezone.utc)
        account.latest_code_folder = "inbox"


def _folder_key(folder: str | None) -> str:
    value = str(folder or "inbox").strip().lower()
    if value in ("junk", "spam", "junkemail", "垃圾", "垃圾邮件"):
        return "spam"
    if value in ("sent", "sentitems", "sent mail", "已发送", "已发"):
        return "sent"
    return "inbox"


def _cached_code_fresh(account: Account, folder: str, settings: Settings) -> bool:
    if not account.latest_verification_code or account.latest_code_at is None:
        return False
    if getattr(account, "latest_code_folder", None) != _folder_key(folder):
        return False
    ttl = float(getattr(settings, "code_api_cache_ttl_seconds", 90.0) or 0.0)
    if ttl <= 0:
        return False
    code_at = account.latest_code_at
    if code_at.tzinfo is None:
        code_at = code_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - code_at).total_seconds()
    return 0 <= age <= ttl


def _mark_error(account: Account, error: str) -> None:
    """Record fetch failure on the account row."""
    account.last_error = (error or "fetch failed")[:500]
    account.updated_at = datetime.now(timezone.utc)
    low = (error or "").lower()
    if "refresh token" in low or "刷新令牌" in (error or "") or "need_reauth" in low:
        account.status = AccountStatus.need_reauth
    elif account.status != AccountStatus.disabled:
        account.status = AccountStatus.error


def _parse_message_date(value: str | None) -> datetime | None:
    """Parse ISO-ish date strings into timezone-aware datetime."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # ISO 8601 / Graph style
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    # RFC 2822 (common in IMAP Date headers)
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fetch_account(
    db: Session,
    account: Account,
    *,
    folder: str = "inbox",
    quick: bool = True,
    force: bool = False,
    keyword: str | None = None,
    custom_regex: str | None = None,
    settings: Settings | None = None,
    use_cache: bool = False,
    force_new_sid: bool = False,
    since: str | None = None,
    before: str | None = None,
    max_messages: int | None = None,
    full: bool = False,
    egress_mode: str = "interactive",
    phase: str = "full",
    body_ids: list[str] | None = None,
    expected_uidvalidity: int | None = None,
) -> FetchServiceResult:
    """Fetch for a stored account: guard → provider → credential write-back → cache."""
    s = settings or get_settings()
    email = account.email

    if account.status == AccountStatus.disabled:
        return FetchServiceResult(
            ok=False,
            error="账号已禁用 / Account disabled",
            email=email,
            account_id=account.id,
            folder=folder,
        )

    # Optional short-cache short-circuit (code API default path) with TTL
    if use_cache and not force and _cached_code_fresh(account, folder, s):
        return FetchServiceResult(
            ok=True,
            code=_latest_code_plain(account, s),
            email=email,
            account_id=account.id,
            folder=folder,
            cached=True,
            fetched_at=account.latest_code_at or datetime.now(timezone.utc),
        )

    try:
        creds = _build_credentials_for_account(
            account, settings=s, db=db, force_new_sid=force_new_sid
        )
    except CryptoError:
        return FetchServiceResult(
            ok=False,
            error="凭证解密失败 / Credential decryption failed",
            email=email,
            account_id=account.id,
            folder=folder,
        )

    # Client-sealed vault credentials: server (and admin) cannot decrypt
    if isinstance(creds, dict) and creds.get("_om_client_sealed"):
        return FetchServiceResult(
            ok=False,
            error=(
                "凭证由客户端保险库加密，服务端无法解密。"
                "请在本机解锁后使用代理取信 / Credentials are client-sealed; "
                "use browser proxy fetch after unlocking the vault"
            ),
            email=email,
            account_id=account.id,
            folder=folder,
        )

    # Infer provider if still unknown
    if account.provider == ProviderType.unknown:
        inferred = infer_provider(provider=account.provider, credentials=creds)
        if inferred != "unknown":
            try:
                account.provider = ProviderType(inferred)
            except ValueError:
                pass

    provider = resolve_provider(account)
    if provider is None:
        return FetchServiceResult(
            ok=False,
            error="未知取件方式，请补全凭证 / Unknown provider; complete credentials",
            email=email,
            account_id=account.id,
            folder=folder,
        )

    # Stored accounts only keep a short code cache, not per-folder message
    # cursors. A mailbox-level last_fetch_at is not a valid high-water mark:
    # fetching inbox first could make junk/sent skip older unseen messages.
    # Keep this path recent-page based until a (account, folder) cursor exists.
    limits = build_fetch_limits(
        since=since,
        before=before,
        full=full,
        quick=quick,
        max_messages=max_messages,
        default_quick=15,
        default_page=50,
    )
    phase_n = (phase or "full").strip().lower() or "full"
    if phase_n in ("headers", "bodies"):
        limits["phase"] = phase_n
    if body_ids:
        limits["body_ids"] = [str(x) for x in body_ids if x]
    if expected_uidvalidity is not None:
        limits["expected_uidvalidity"] = expected_uidvalidity

    try:
        from app.services.settings_service import get_effective_settings

        skip_interval = phase_n == "bodies"
        with account_fetch_slot(
            db,
            account.id,
            settings=s,
            force=force or skip_interval,
            folder=folder,
        ) as lease_token:
            ptype_name = str(getattr(account.provider, "value", account.provider) or "")
            try:
                eff = get_effective_settings(db, settings=s)
                ordered = resolve_egress_candidates(
                    account,
                    settings=eff,
                    provider=ptype_name,
                    egress_mode=egress_mode,
                    force_new_sid=force_new_sid,
                )
            except Exception:
                fixed_proxy = str(creds.get("proxy") or account.proxy or "").strip()
                ordered = [fixed_proxy] if fixed_proxy else [None]

            result = FetchResult(ok=False, folder=folder, error="取件失败 / Fetch failed")
            for idx, egress in enumerate(ordered):
                c = dict(creds)
                c.pop("_fixed_proxy", None)
                if egress:
                    c["proxy"] = egress
                else:
                    c.pop("proxy", None)
                try:
                    result = provider.fetch(
                        account,
                        folder=folder,
                        quick=quick,
                        credentials=c,
                        limits=limits or None,
                    )
                except ProviderNotImplemented as exc:
                    result = FetchResult(ok=False, folder=folder, error=str(exc))
                    break
                except Exception as exc:  # noqa: BLE001 — surface clean error
                    result = FetchResult(
                        ok=False,
                        folder=folder,
                        error=f"取件异常 / Fetch error: {exc.__class__.__name__}",
                    )
                if result.ok:
                    break
                remaining = idx + 1 < len(ordered)
                can_retry = (
                    _is_direct_failover_error(result.error)
                    if egress is None
                    else _is_retryable_egress_error(result.error)
                )
                if remaining and can_retry:
                    continue
                break

            if lease_token and not lease_is_current(
                db, account.id, lease_token, settings=s, folder=folder
            ):
                raise FetchInFlightError()

            if result.credential_updates and result.credential_updates.any():
                # Merge token_expires into credential blob if present in session_meta
                if lease_token and not lease_is_current(
                    db, account.id, lease_token, settings=s, folder=folder
                ):
                    raise FetchInFlightError()
                apply_credential_updates(
                    db,
                    account,
                    result.credential_updates,
                    settings=s,
                    base_credentials=creds,
                )
                # Persist expires_at convenience field on credential JSON
                meta = result.credential_updates.session_meta or {}
                if (
                    result.credential_updates.access_token
                    or result.credential_updates.refresh_token
                    or meta.get("oauth_transport")
                ):
                    from app.services.credentials import load_credentials, save_credentials

                    updated = load_credentials(account, settings=s)
                    if result.credential_updates.access_token:
                        updated["access_token"] = result.credential_updates.access_token
                    if result.credential_updates.refresh_token:
                        updated["refresh_token"] = result.credential_updates.refresh_token
                    if meta.get("oauth_transport"):
                        updated["oauth_transport"] = str(meta["oauth_transport"])
                    if meta.get("token_expires_in") is not None:
                        try:
                            exp_in = int(meta["token_expires_in"])
                            updated["token_expires_at"] = (
                                datetime.now(timezone.utc).timestamp() + exp_in
                            )
                        except (TypeError, ValueError):
                            pass
                    save_credentials(account, updated, settings=s)

            if not result.ok:
                if phase_n != "bodies":
                    _mark_error(account, result.error or "fetch failed")
                    db.commit()
                return FetchServiceResult(
                    ok=False,
                    error=result.error or "取件失败 / Fetch failed",
                    email=email,
                    account_id=account.id,
                    folder=result.folder or folder,
                    messages=result.messages,
                    phase=phase_n,
                    pending_body_ids=list(getattr(result, "pending_body_ids", None) or []),
                    partial=bool(getattr(result, "partial", False)),
                    uidvalidity=getattr(result, "uidvalidity", None),
                )

            for m in result.messages:
                if not m.verification_code:
                    annotate_message_code(m, custom_regex=custom_regex)

            if lease_token and not lease_is_current(
                db, account.id, lease_token, settings=s, folder=folder
            ):
                raise FetchInFlightError()

            code = None
            matched = None
            if phase_n != "headers":
                code, matched = _pick_best_code(
                    result.messages, keyword=keyword, custom_regex=custom_regex
                )
                _write_short_cache(
                    db,
                    account,
                    result.messages,
                    code,
                    matched=matched,
                    folder=result.folder or folder,
                )
            else:
                _mark_fetch_ok(account)
            db.commit()
            session = _session_fields_from_result(result)
            pending = list(getattr(result, "pending_body_ids", None) or [])

            return FetchServiceResult(
                ok=True,
                messages=result.messages,
                folder=result.folder or folder,
                fetched_at=result.fetched_at,
                code=code,
                cached=False,
                email=email,
                account_id=account.id,
                subject=matched.subject if matched else None,
                from_=matched.from_ if matched else None,
                date=matched.date if matched else None,
                session_cookies=session["session_cookies"],
                session_meta=session["session_meta"],
                session_restored=session["session_restored"],
                mailboxes=session.get("mailboxes"),
                uidvalidity=getattr(result, "uidvalidity", None),
                credential_updates=session.get("credential_updates"),
                phase=phase_n,
                pending_body_ids=pending,
                partial=bool(pending) or phase_n == "headers",
            )
    except FetchTooSoonError as exc:
        # Fall back to cache if available
        if _cached_code_fresh(account, folder, s):
            return FetchServiceResult(
                ok=True,
                code=_latest_code_plain(account, s),
                email=email,
                account_id=account.id,
                folder=folder,
                cached=True,
                too_soon=True,
                retry_after=exc.retry_after,
                fetched_at=account.latest_code_at or datetime.now(timezone.utc),
            )
        return FetchServiceResult(
            ok=False,
            error=f"请求过于频繁，请 {exc.retry_after:.0f}s 后重试 / Too soon, retry in {exc.retry_after:.0f}s",
            email=email,
            account_id=account.id,
            folder=folder,
            too_soon=True,
            retry_after=exc.retry_after,
        )
    except FetchInFlightError as exc:
        retry = exc.retry_after
        if _cached_code_fresh(account, folder, s):
            return FetchServiceResult(
                ok=True,
                code=_latest_code_plain(account, s),
                email=email,
                account_id=account.id,
                folder=folder,
                cached=True,
                retry_after=retry,
            )
        if retry is not None and retry > 0:
            err = (
                f"取件进行中，请 {retry:.0f}s 后重试 / "
                f"Fetch in progress, retry in {retry:.0f}s"
            )
        else:
            err = "取件进行中，请稍后 / Fetch already in progress"
        return FetchServiceResult(
            ok=False,
            error=err,
            email=email,
            account_id=account.id,
            folder=folder,
            retry_after=retry,
        )


def _is_retryable_egress_error(err: str | None) -> bool:
    """True when another WARP channel or direct egress may still succeed.

    Does NOT retry clear credential failures (wrong password / app password).
    """
    if not err:
        return False
    e = err.lower()
    # Hard credential errors — do not burn the whole proxy pool
    hard = (
        "账号或密码错误",
        "invalid credentials",
        "imap 认证失败",
        "认证失败，请检查授权码",
        "授权码",
        "password incorrect",
        "invalid password",
        "wrong password",
        "need_reauth",
        "刷新令牌",
        "refresh token",
    )
    if any(x in e or x in (err or "") for x in hard):
        # rate-limit pages sometimes embed "password" marketing — allow retry if also transient
        if not any(
            t in e
            for t in ("parse", "rate", "captcha", "频繁", "稍后", "timeout", "proxy", "socks")
        ):
            return False
    markers = (
        "connecterror",
        "connect error",
        "proxy",
        "socks",
        "name or service not known",
        "nodename nor servname",
        "connection refused",
        "network is unreachable",
        "failed to establish",
        "timeout",
        "timed out",
        "login parse failed",
        "parse failed",
        "未返回",
        "ott",
        "访问过于频繁",
        "验证码",
        "captcha",
        "rate",
        "稍后",
        "登录不稳定",
        "session",
        "登录失败",
        "login failed",
        "请求失败",
        "连接",
        "取信失败",
        "fetch error",
        "取件异常",
    )
    return any(x in e or x in (err or "") for x in markers)


def _cap_egress_candidates(
    candidates: list[str | None],
    *,
    max_attempts: int = MAX_EGRESS_ATTEMPTS,
) -> list[str | None]:
    """Cap the walk while preserving caller order, always keeping direct if present.

    Direct-first input ``[None, w1, w2, …]`` stays direct-first.
    WARP-first input ``[w1, w2, …, None]`` keeps direct last.
    """
    if max_attempts < 1:
        max_attempts = 1
    seen: set[str] = set()
    ordered: list[str | None] = []
    for p in candidates:
        key = p if p is not None else "__direct__"
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
    if not ordered:
        return [None]
    if len(ordered) <= max_attempts:
        return ordered
    has_direct = None in ordered
    if not has_direct:
        return ordered[:max_attempts]
    others = [p for p in ordered if p is not None]
    if ordered[0] is None:
        return [None, *others[: max_attempts - 1]]
    return [*others[: max_attempts - 1], None]


def _is_direct_failover_error(err: str | None) -> bool:
    """True when a failed *direct* hop may succeed via WARP.

    Narrower than ``_is_retryable_egress_error``: auth, Graph 429, and generic
    ``请求失败`` / ``login failed`` must not walk the pool.
    """
    if not err:
        return False
    e = err.lower()
    hard = (
        "账号或密码错误",
        "invalid credentials",
        "imap 认证失败",
        "认证失败，请检查授权码",
        "password incorrect",
        "invalid password",
        "wrong password",
        "need_reauth",
        "刷新令牌",
        "refresh token",
        "invalid_grant",
        "权限不足",
        "unauthorized",
        "forbidden",
    )
    if any(x in e or x in (err or "") for x in hard):
        return False
    markers = (
        "timeout",
        "timed out",
        "connection refused",
        "network is unreachable",
        "failed to establish",
        "connecterror",
        "connect error",
        "connection reset",
        "broken pipe",
        "eof occurred",
        "socket error",
        "eof",
        "nodename nor servname",
        "name or service not known",
        "421",
        "socks",
        "proxy",
        "tls",
        "ssl",
        "handshake",
        "imap 连接超时",
        "网络错误",
        "network error",
    )
    return any(x in e for x in markers)


def resolve_egress_candidates(
    account: Any,
    *,
    settings: Any,
    provider: str,
    egress_mode: str = "interactive",
    force_new_sid: bool = False,
) -> list[str | None]:
    """Build the capped egress walk for this fetch.

    Interactive IMAP/OAuth/http_api: direct → sticky WARP → alternate.
    Bulk, and all cookie/unknown: WARP-first (sticky → alternate → direct).
    """
    from app.services.proxy import list_proxy_candidates

    ptype = (provider or "").strip().lower()
    mode = (egress_mode or "interactive").strip().lower()
    cookie_like = ptype in ("cookie", "unknown")
    prefer_direct = mode != "bulk" and ptype in ("imap", "oauth")
    try:
        candidates = list_proxy_candidates(
            account,
            settings=settings,
            force_new_sid=force_new_sid,
            include_direct=True,
            prefer_direct=prefer_direct,
        )
    except Exception:
        fixed = str(getattr(account, "proxy", None) or "").strip()
        candidates = [fixed] if fixed else [None]
    cap = MAX_EGRESS_ATTEMPTS_COOKIE if cookie_like else MAX_EGRESS_ATTEMPTS
    return _cap_egress_candidates(candidates, max_attempts=cap)


def _session_fields_from_result(result: FetchResult) -> dict[str, Any]:
    """Extract cookie / mailbox write-back for local-first clients."""
    out: dict[str, Any] = {
        "session_restored": bool(getattr(result, "session_restored", False)),
        "session_cookies": None,
        "session_meta": None,
        "mailboxes": None,
        "credential_updates": None,
    }
    updates = getattr(result, "credential_updates", None)
    if updates is None:
        return out
    if updates.session_cookies is not None:
        out["session_cookies"] = updates.session_cookies
    if updates.session_meta is not None:
        out["session_meta"] = updates.session_meta
        # mailboxes may only be in session_meta
        mb = updates.session_meta.get("mailboxes")
        if isinstance(mb, list):
            out["mailboxes"] = [str(x) for x in mb if x]
    if getattr(updates, "mailboxes", None) is not None:
        out["mailboxes"] = [str(x) for x in (updates.mailboxes or []) if x]
    cred: dict[str, str] = {}
    if updates.refresh_token:
        cred["refresh_token"] = str(updates.refresh_token)
    if updates.access_token:
        cred["access_token"] = str(updates.access_token)
    transport = (updates.session_meta or {}).get("oauth_transport")
    if transport:
        cred["oauth_transport"] = str(transport)
    out["credential_updates"] = cred or None
    return out


def fetch_proxy(
    *,
    email: str,
    provider: Any = None,
    password: str | None = None,
    credential: dict[str, Any] | None = None,
    cookies: list[dict[str, Any]] | None = None,
    folder: str = "inbox",
    quick: bool = True,
    keyword: str | None = None,
    custom_regex: str | None = None,
    settings: Settings | None = None,
    proxy: str | None = None,
    since: str | None = None,
    before: str | None = None,
    max_messages: int | None = None,
    full: bool = False,
    egress_mode: str = "interactive",
    phase: str = "full",
    body_ids: list[str] | None = None,
    expected_uidvalidity: int | None = None,
) -> FetchServiceResult:
    """Guest/proxy fetch: credentials in memory only, never persisted.

    Interactive IMAP/OAuth: direct → sticky WARP → alternate.
    Bulk and cookie: sticky WARP → alternate → direct.
    Fixed per-request proxy is tried once (no rotation).
    """
    s = settings or get_settings()
    creds = merge_guest_credentials(
        password=password, credential=credential, cookies=cookies
    )
    # Accept cookies nested in credential (local-first write-back shape)
    if not creds.get("cookies") and isinstance(credential, dict):
        nested = (
            credential.get("cookies")
            or credential.get("session_cookies")
            or credential.get("session")
        )
        if nested:
            creds["cookies"] = nested
    if isinstance(credential, dict) and credential.get("session_meta") and not creds.get(
        "session_meta"
    ):
        creds["session_meta"] = credential.get("session_meta")
    creds.setdefault("email", email)
    ptype = infer_provider(provider=provider, credentials=creds, password=password)

    # Synthetic account — do NOT put pool URL into account.proxy (that freezes rotation).
    # account.proxy is only for explicit fixed override from the request body.
    fixed_proxy = str(proxy).strip() if proxy and str(proxy).strip() else None
    account = SimpleNamespace(
        id=None,
        email=email,
        provider=ptype,
        password_enc=None,
        credential_enc=None,
        session=None,
        proxy=fixed_proxy,
        status=AccountStatus.ok,
    )

    try:
        from app.services.settings_service import get_effective_settings

        eff = get_effective_settings(None, settings=s)
        ordered = resolve_egress_candidates(
            account,
            settings=eff,
            provider=ptype,
            egress_mode=egress_mode,
        )
    except Exception:
        ordered = [fixed_proxy] if fixed_proxy else [None]

    # Build a thin wrapper matching ProviderType for resolve_provider
    try:
        account.provider = ProviderType(ptype)  # type: ignore[attr-defined]
    except ValueError:
        account.provider = ProviderType.unknown  # type: ignore[attr-defined]

    provider_impl = resolve_provider(account)
    if provider_impl is None:
        return FetchServiceResult(
            ok=False,
            error="未知取件方式，请补全凭证 / Unknown provider; complete credentials",
            email=email,
            folder=folder,
        )

    limits = build_fetch_limits(
        since=since,
        before=before,
        full=full,
        quick=quick,
        max_messages=max_messages,
        default_quick=20,
        default_page=50,
    )
    phase_n = (phase or "full").strip().lower() or "full"
    if phase_n in ("headers", "bodies"):
        limits["phase"] = phase_n
    if body_ids:
        limits["body_ids"] = [str(x) for x in body_ids if x]
    if expected_uidvalidity is not None:
        limits["expected_uidvalidity"] = expected_uidvalidity

    def _do_fetch(c: dict[str, Any]) -> FetchResult:
        return provider_impl.fetch(
            account,
            folder=folder,
            quick=quick,
            credentials=c,
            limits=limits or None,
        )

    if not ordered:
        ordered = [None]

    result: FetchResult | None = None
    last_err: str | None = None
    for idx, egress in enumerate(ordered):
        c = dict(creds)
        if egress:
            c["proxy"] = egress
        else:
            c.pop("proxy", None)
        try:
            result = _do_fetch(c)
        except ProviderNotImplemented as exc:
            return FetchServiceResult(ok=False, error=str(exc), email=email, folder=folder)
        except Exception as exc:  # noqa: BLE001
            last_err = f"取件异常 / Fetch error: {exc.__class__.__name__}"
            result = FetchResult(ok=False, folder=folder, error=last_err)
        if result.ok:
            break
        last_err = result.error or last_err
        remaining = idx + 1 < len(ordered)
        can_retry = (
            _is_direct_failover_error(result.error)
            if egress is None
            else _is_retryable_egress_error(result.error)
        )
        if remaining and can_retry:
            continue
        break

    assert result is not None
    pending = list(getattr(result, "pending_body_ids", None) or [])
    if not result.ok:
        return FetchServiceResult(
            ok=False,
            error=result.error or last_err or "取件失败 / Fetch failed",
            email=email,
            folder=result.folder or folder,
            messages=result.messages,
            phase=phase_n,
            pending_body_ids=pending,
            partial=bool(getattr(result, "partial", False)),
            uidvalidity=getattr(result, "uidvalidity", None),
        )

    for m in result.messages:
        if not m.verification_code:
            annotate_message_code(m, custom_regex=custom_regex)
    code = None
    matched = None
    if phase_n != "headers":
        code, matched = _pick_best_code(
            result.messages, keyword=keyword, custom_regex=custom_regex
        )
    session = _session_fields_from_result(result)
    return FetchServiceResult(
        ok=True,
        messages=result.messages,
        folder=result.folder or folder,
        fetched_at=result.fetched_at,
        code=code,
        cached=False,
        email=email,
        subject=matched.subject if matched else None,
        from_=matched.from_ if matched else None,
        date=matched.date if matched else None,
        session_cookies=session["session_cookies"],
        session_meta=session["session_meta"],
        session_restored=session["session_restored"],
        mailboxes=session.get("mailboxes"),
        uidvalidity=getattr(result, "uidvalidity", None),
        credential_updates=session.get("credential_updates"),
        phase=phase_n,
        pending_body_ids=pending,
        partial=bool(pending) or phase_n == "headers",
    )
