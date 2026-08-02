"""Fetch orchestration: decrypt credentials, route provider, apply updates, cache codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import CryptoError
from app.fetch_guard import (
    FetchInFlightError,
    FetchTooSoonError,
    account_fetch_slot,
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
    merge_guest_credentials,
)
from app.services.parser import annotate_message_code, extract_verification_code


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


def _write_short_cache(
    db: Session,
    account: Account,
    messages: list[Message],
    code: str | None,
    *,
    matched: Message | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    account.last_fetch_at = now
    account.last_error = None
    if account.status == AccountStatus.error:
        account.status = AccountStatus.ok
    if code:
        account.latest_verification_code = code
        account.latest_code_at = now
    account.updated_at = now

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

    # Optional short-cache short-circuit (code API default path)
    if use_cache and not force and account.latest_verification_code:
        return FetchServiceResult(
            ok=True,
            code=account.latest_verification_code,
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

    # Incremental: after first successful fetch, only pull recent window
    limits: dict[str, Any] = {}
    lookback = int(getattr(s, "fetch_default_lookback_days", 3) or 3)
    if account.last_fetch_at and lookback > 0 and not force:
        from datetime import timedelta

        since_dt = datetime.now(timezone.utc) - timedelta(days=lookback)
        # also not older than last_fetch_at - 1 day slack
        try:
            lf = account.last_fetch_at
            if lf.tzinfo is None:
                lf = lf.replace(tzinfo=timezone.utc)
            slack = lf - timedelta(days=1)
            if slack > since_dt:
                since_dt = slack
        except Exception:
            pass
        limits["since"] = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if quick:
        limits["max_messages"] = 15

    try:
        with account_fetch_slot(db, account.id, settings=s, force=force):
            # Walk sticky WARP → remaining pool → direct (unless fixed account.proxy)
            try:
                from app.services.proxy import list_proxy_candidates
                from app.services.settings_service import get_effective_settings

                eff = get_effective_settings(db, settings=s)
                # Temporarily clear account.proxy on the object used for pool listing
                # when we want pool rotation — but fixed account.proxy must stick.
                candidates = list_proxy_candidates(
                    account,
                    settings=eff,
                    force_new_sid=force_new_sid,
                    include_direct=True,
                )
            except Exception:
                candidates = [creds.get("proxy") or account.proxy, None]

            seen_p: set[str] = set()
            ordered: list[str | None] = []
            for p in candidates:
                key = p if p is not None else "__direct__"
                if key in seen_p:
                    continue
                seen_p.add(key)
                ordered.append(p)
            if not ordered:
                ordered = [None]

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
                if idx + 1 < len(ordered) and _is_retryable_egress_error(result.error):
                    continue
                break

            if result.credential_updates and result.credential_updates.any():
                # Merge token_expires into credential blob if present in session_meta
                apply_credential_updates(
                    db,
                    account,
                    result.credential_updates,
                    settings=s,
                    base_credentials=creds,
                )
                # Persist expires_at convenience field on credential JSON
                meta = result.credential_updates.session_meta or {}
                if result.credential_updates.access_token or result.credential_updates.refresh_token:
                    from app.services.credentials import load_credentials, save_credentials

                    updated = load_credentials(account, settings=s)
                    if result.credential_updates.access_token:
                        updated["access_token"] = result.credential_updates.access_token
                    if result.credential_updates.refresh_token:
                        updated["refresh_token"] = result.credential_updates.refresh_token
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
                _mark_error(account, result.error or "fetch failed")
                db.commit()
                return FetchServiceResult(
                    ok=False,
                    error=result.error or "取件失败 / Fetch failed",
                    email=email,
                    account_id=account.id,
                    folder=result.folder or folder,
                    messages=result.messages,
                )

            for m in result.messages:
                if not m.verification_code:
                    annotate_message_code(m, custom_regex=custom_regex)

            code, matched = _pick_best_code(
                result.messages, keyword=keyword, custom_regex=custom_regex
            )
            _write_short_cache(db, account, result.messages, code, matched=matched)
            db.commit()
            session = _session_fields_from_result(result)

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
            )
    except FetchTooSoonError as exc:
        # Fall back to cache if available
        if account.latest_verification_code:
            return FetchServiceResult(
                ok=True,
                code=account.latest_verification_code,
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
    except FetchInFlightError:
        if account.latest_verification_code:
            return FetchServiceResult(
                ok=True,
                code=account.latest_verification_code,
                email=email,
                account_id=account.id,
                folder=folder,
                cached=True,
            )
        return FetchServiceResult(
            ok=False,
            error="取件进行中，请稍后 / Fetch already in progress",
            email=email,
            account_id=account.id,
            folder=folder,
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


def _session_fields_from_result(result: FetchResult) -> dict[str, Any]:
    """Extract cookie / mailbox write-back for local-first clients."""
    out: dict[str, Any] = {
        "session_restored": bool(getattr(result, "session_restored", False)),
        "session_cookies": None,
        "session_meta": None,
        "mailboxes": None,
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
    full: bool = False,
) -> FetchServiceResult:
    """Guest/proxy fetch: credentials in memory only, never persisted.

    Egress strategy when admin proxy_pool is set:
      sticky WARP → remaining WARP workers → direct (None)
    Fixed per-request proxy is tried once, then direct.

    Session cookies from credential / cookies body are preferred; on success
    rolling cookies are returned for the client to persist and reuse.
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
        from app.services.proxy import list_proxy_candidates
        from app.services.settings_service import get_effective_settings

        eff = get_effective_settings(None, settings=s)
        # candidates: [warp sticky, warp…, None(direct)] or [fixed, None]
        candidates = list_proxy_candidates(
            account, settings=eff, include_direct=True
        )
    except Exception:
        candidates = [fixed_proxy] if fixed_proxy else [None]
        if fixed_proxy:
            candidates.append(None)

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

    limits: dict[str, Any] = {}
    if since and not full:
        limits["since"] = since
    elif not full and quick:
        limits["max_messages"] = 15
    if quick:
        limits.setdefault("max_messages", 15)

    def _do_fetch(c: dict[str, Any]) -> FetchResult:
        return provider_impl.fetch(
            account,
            folder=folder,
            quick=quick,
            credentials=c,
            limits=limits or None,
        )

    # Deduplicate while preserving order (list_proxy_candidates already does)
    seen: set[str] = set()
    ordered: list[str | None] = []
    for p in candidates:
        key = p if p is not None else "__direct__"
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
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
        # Stop early on clear credential failure; otherwise try next WARP / direct
        if idx + 1 < len(ordered) and _is_retryable_egress_error(result.error):
            continue
        break

    assert result is not None
    if not result.ok:
        return FetchServiceResult(
            ok=False,
            error=result.error or last_err or "取件失败 / Fetch failed",
            email=email,
            folder=result.folder or folder,
            messages=result.messages,
        )

    for m in result.messages:
        if not m.verification_code:
            annotate_message_code(m, custom_regex=custom_regex)
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
    )
