"""HttpApi provider: fetch api_url with SSRF protection and normalize messages.

Supports generic JSON shapes:
- { "messages": [ ... ] }
- { "data": [ ... ] } or { "data": { "messages": [...] } }
- root array [ ... ]
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any

import httpx

from app.providers.base import CredentialUpdates, FetchResult, HealthResult, Message
from app.services.parser import annotate_message_code
from app.services.ssrf import SsrfError, validate_redirect_target, validate_url

_TIMEOUT = 20.0
_MAX_REDIRECTS = 5
_MAX_BODY_BYTES = 2_000_000
_CANDIDATE_DEADLINE = 30.0
_SENSITIVE_REDIRECT_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "x-api-key",
    "x-admin-auth", "x-custom-auth",
}

# When user imports only the Worker origin (https://xxx.workers.dev), try these
# JSON endpoints with the same auth headers (cf_temp_email / MoeMail / generic).
_WORKER_API_PATH_CANDIDATES = (
    "/api/mails",
    "/api/mail",
    "/api/emails",
    "/api/messages",
    "/api/address",
    "/api/mails?limit=50",
    "/api/emails?limit=50",
    "/admin_api/mails",
    "/open_api/mails",
    "/api/v1/mails",
    "/api/public/mails",
    "/api/mailboxes",
    "/api/settings",
)


def expand_api_url_candidates(api_url: str) -> list[str]:
    """If api_url is a bare origin (no useful path), return origin + common API paths.

    Users often import only ``https://xxx.workers.dev`` — the admin UI HTML lives
    at ``/`` while JSON is under ``/api/*``. Always try the original URL first.
    """
    raw = (api_url or "").strip()
    if not raw:
        return []
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(raw)
    except Exception:
        return [raw]
    path = (parsed.path or "").rstrip("/") or ""
    # Has a real path (not empty / bare /) — use as-is only
    if path and path not in ("", "/"):
        return [raw]
    # Bare host / trailing slash only
    base = urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")
    if not base:
        return [raw]
    out: list[str] = [raw if raw.endswith("/") else raw]  # keep user URL first
    if base + "/" not in out and base not in out:
        out.insert(0, base)
    for p in _WORKER_API_PATH_CANDIDATES:
        u = f"{base}{p}"
        if u not in out:
            out.append(u)
    return out


def build_api_auth_headers(creds: dict[str, Any]) -> dict[str, str]:
    """Build outbound headers for HttpApi (CF Worker / self-hosted).

    Supports both **open** APIs (no secret) and common auth styles:

    - ``headers`` dict as-is (highest priority for explicit keys)
    - ``api_key`` / ``token`` / ``password`` / ``auth_code`` as the secret
    - ``auth_header`` or ``api_auth_style`` to pick one scheme; default sends
      several common headers so one secret works with MoeMail, cf_temp_email, etc.

    Styles: ``none`` | ``x-admin-auth`` | ``x-api-key`` | ``bearer`` |
    ``x-custom-auth`` | ``authorization`` | ``auto`` (default multi-header).
    """
    headers: dict[str, str] = {}
    raw = creds.get("headers")
    if isinstance(raw, dict):
        headers = {str(k): str(v) for k, v in raw.items() if v is not None}

    secret = (
        creds.get("api_key")
        or creds.get("token")
        or creds.get("auth_code")
        or creds.get("password")
        or ""
    )
    secret = str(secret).strip()
    if not secret:
        return headers

    style = str(
        creds.get("auth_header")
        or creds.get("api_auth_style")
        or creds.get("auth_style")
        or "auto"
    ).strip().lower()

    def _set(name: str, value: str) -> None:
        # Do not override explicitly provided headers
        if name not in headers and name.lower() not in {k.lower() for k in headers}:
            headers[name] = value

    if style in ("none", "off", "open", "public"):
        return headers
    if style in ("x-admin-auth", "admin", "cf_temp_email", "cloudflare_temp_email"):
        _set("x-admin-auth", secret)
    elif style in ("x-api-key", "api-key", "apikey", "moemail"):
        _set("X-API-Key", secret)
    elif style in ("x-custom-auth", "custom-auth", "site"):
        _set("x-custom-auth", secret)
    elif style in ("bearer", "authorization-bearer"):
        _set("Authorization", f"Bearer {secret}")
    elif style in ("authorization", "basic"):
        # raw Authorization value if user already formatted it
        if secret.lower().startswith("bearer ") or secret.lower().startswith("basic "):
            _set("Authorization", secret)
        else:
            _set("Authorization", f"Bearer {secret}")
    else:
        # auto: cover common CF temp-mail / MoeMail / generic API patterns
        _set("x-admin-auth", secret)
        _set("X-API-Key", secret)
        _set("x-custom-auth", secret)
        _set("Authorization", f"Bearer {secret}")
    return headers


def _provider_value(account: Any) -> str:
    p = getattr(account, "provider", None)
    if p is None:
        return ""
    return str(getattr(p, "value", p))


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v)


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _format_address_list(raw: Any) -> str:
    """Normalize to/from recipient fields: str | list | dict → display string."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        addr = _as_str(_pick(raw, "address", "email", "emailAddress", "mail"), default="")
        name = _as_str(_pick(raw, "name", "display", "displayName"), default="")
        if name and addr:
            return f"{name} <{addr}>"
        return addr or name
    if isinstance(raw, list):
        parts: list[str] = []
        for x in raw:
            s = _format_address_list(x)
            if s:
                parts.append(s)
        return ", ".join(parts)
    return _as_str(raw)


def _normalize_date_value(raw: Any) -> str | None:
    """Coerce Worker/API date fields to a parseable string (ISO preferred)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        # epoch seconds vs ms
        v = float(raw)
        ms = v if abs(v) >= 100_000_000_000 else v * 1000.0
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(raw)
    s = _as_str(raw).strip()
    if not s:
        return None
    # SQLite-style "2026-08-04 09:12:17" → ISO-ish for JS Date.parse
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s) and "T" not in s:
        s = s.replace(" ", "T", 1)
        if not s.endswith("Z") and "+" not in s[10:]:
            s = s + "Z"
    return s


def normalize_message_item(item: dict[str, Any], *, folder: str = "inbox", index: int = 0) -> Message:
    """Map a generic JSON message object to Message.

    Aligns with CF temp-mail workers (ian10-mail-admin / MoeMail-style):

    - ``recipient`` / ``address`` → to
    - ``created_at`` / ``createdAt`` → date
    - ``text`` → body_text; ``code`` → verification_code
    """
    raw_id = _pick(item, "id", "message_id", "uid", "messageId")
    if raw_id is None or not str(raw_id).strip():
        # Content-derived identity remains stable when upstream omits IDs.
        identity = {k: item.get(k) for k in (
            "subject", "from", "from_address", "sender", "to", "recipient",
            "date", "created_at", "createdAt", "received_at", "receivedAt",
            "body", "body_text", "body_html", "text",
        ) if item.get(k) is not None}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:24]
        mid = f"http-{digest}"
    else:
        mid = _as_str(raw_id)
    subject = _as_str(_pick(item, "subject", "title", "Subject"), default="")
    from_raw = _pick(item, "from", "from_", "sender", "from_address", "fromAddress", "source")
    from_display = ""
    from_address = ""
    if isinstance(from_raw, dict):
        from_address = _as_str(_pick(from_raw, "address", "email", "emailAddress"), default="")
        from_display = _as_str(_pick(from_raw, "name", "display"), default=from_address)
    elif isinstance(from_raw, str):
        from_display = from_raw
        # crude email extract
        if "<" in from_raw and ">" in from_raw:
            inner = from_raw[from_raw.rfind("<") + 1 : from_raw.rfind(">")]
            from_address = inner.strip()
        elif "@" in from_raw:
            from_address = from_raw.strip()
    else:
        from_address = _as_str(_pick(item, "from_address", "fromAddress", "sender_email"), default="")
        from_display = from_address

    # ian10-mail-admin list rows: recipient = temp mailbox address (收件人)
    # Prefer explicit to/recipient over bare "address" so mailbox list objects
    # are not mistaken when both exist.
    to_raw = _pick(
        item,
        "to",
        "to_address",
        "toAddress",
        "recipient",
        "recipients",
        "to_mail",
        "toMail",
        "mailbox",
        "address",  # CF raw_mails.address when exposed as-is
        default="",
    )
    to_str = _format_address_list(to_raw)

    body_text = _as_str(
        _pick(item, "body_text", "bodyText", "text", "textBody", "plain", "plainText"),
        default="",
    )
    body_html = _as_str(
        _pick(item, "body_html", "bodyHtml", "html", "htmlBody", "rawHtml", "source_html"),
        default="",
    )
    # Generic "body" / "content" — may be HTML or plain depending on Worker
    # Note: do not use "source" alone as body — ian10 uses source as From fallback
    generic_body = _as_str(_pick(item, "body", "content", "message", "raw"), default="")
    if generic_body and not body_html and not body_text:
        if re.search(r"<(?:html|body|div|p|br|table)\b", generic_body, re.I) or generic_body.lstrip().startswith(
            "<"
        ):
            body_html = generic_body
        else:
            body_text = generic_body
    elif generic_body and not body_html and "<" in generic_body and len(generic_body) > len(body_text):
        body_html = generic_body

    preview = _as_str(_pick(item, "body_preview", "bodyPreview", "preview", "snippet"), default="")
    if body_html and not body_text:
        stripped = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", body_html)
        stripped = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", stripped)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        body_text = re.sub(r"\s+", " ", stripped).strip()
    if not preview and body_text:
        preview = body_text[:200]
    # CF worker (ian10-mail-admin): created_at; also createdAt / received_at / time
    date = _pick(
        item,
        "date",
        "created_at",
        "createdAt",
        "received_at",
        "receivedAt",
        "time",
        "timestamp",
        "sent_at",
        "sentAt",
    )
    date_str = _normalize_date_value(date)
    code = _pick(item, "verification_code", "code", "otp")
    code_str = _as_str(code) if code is not None else None
    # Ignore bogus year-only codes from marketing (parser also filters; belt)
    if code_str and re.fullmatch(r"(?:19|20)\d{2}", code_str):
        code_str = None

    msg = Message(
        id=mid,
        subject=subject,
        from_=from_display,
        from_address=from_address,
        to=to_str,
        date=date_str,
        body_preview=preview,
        body_text=body_text or preview,
        body_html=body_html,
        folder=_as_str(_pick(item, "folder"), default=folder) or folder,
        verification_code=code_str or None,
    )
    if not msg.verification_code:
        annotate_message_code(msg)
    return msg


def _looks_like_message(item: dict[str, Any]) -> bool:
    return any(
        k in item
        for k in (
            "subject",
            "Subject",
            "body",
            "body_text",
            "bodyText",
            "body_html",
            "text",
            "html",
            "from",
            "from_",
            "sender",
            "verification_code",
            "code",
            "otp",
            "raw",
            "source",
        )
    )


def _looks_like_mailbox(item: dict[str, Any]) -> bool:
    if _looks_like_message(item):
        return False
    return any(
        k in item
        for k in (
            "address",
            "email",
            "mail",
            "mailbox",
            "box",
            "name",
            "to",
        )
    )


def extract_message_list(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of message dicts from common JSON envelopes."""
    if isinstance(payload, list):
        dicts = [x for x in payload if isinstance(x, dict)]
        msgs = [x for x in dicts if _looks_like_message(x)]
        return msgs if msgs else dicts
    if not isinstance(payload, dict):
        return []
    for key in ("messages", "mails", "items", "results", "parsed_mails", "emails"):
        val = payload.get(key)
        if isinstance(val, list):
            dicts = [x for x in val if isinstance(x, dict)]
            # "emails" may be mailboxes (address only) — skip if not messages
            if key == "emails" and dicts and all(_looks_like_mailbox(x) for x in dicts):
                continue
            msgs = [x for x in dicts if _looks_like_message(x)]
            if msgs or key != "emails":
                return msgs if msgs else dicts
    data = payload.get("data")
    if isinstance(data, list):
        dicts = [x for x in data if isinstance(x, dict)]
        msgs = [x for x in dicts if _looks_like_message(x)]
        return msgs if msgs else dicts
    if isinstance(data, dict):
        for key in ("messages", "mails", "items", "results", "emails"):
            val = data.get(key)
            if isinstance(val, list):
                dicts = [x for x in val if isinstance(x, dict)]
                if key == "emails" and dicts and all(_looks_like_mailbox(x) for x in dicts):
                    continue
                msgs = [x for x in dicts if _looks_like_message(x)]
                return msgs if msgs else dicts
    # Single message object
    if _looks_like_message(payload):
        return [payload]
    return []


def _mailbox_address(item: dict[str, Any]) -> str | None:
    raw = _pick(
        item,
        "address",
        "email",
        "mail",
        "mailbox",
        "box",
        "name",
        "to",
        "to_address",
        "toAddress",
    )
    if isinstance(raw, dict):
        raw = _pick(raw, "address", "email", "name")
    s = _as_str(raw).strip().lower()
    if s and "@" in s and " " not in s:
        return s
    return None


def extract_mailbox_list(payload: Any) -> list[str]:
    """Discover temp-mailbox addresses from CF Worker / multi-inbox API JSON.

    Common shapes:
    - { "emails": [ { "address": "a@x.com" }, ... ] }  (MoeMail)
    - { "mailboxes": [ "a@x.com", ... ] }
    - { "data": { "addresses": [...] } }
    - message list with ``to`` fields (derive unique recipients)
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(addr: str | None) -> None:
        if not addr or addr in seen:
            return
        seen.add(addr)
        found.append(addr)

    def _from_list(val: Any) -> None:
        if not isinstance(val, list):
            return
        for x in val:
            if isinstance(x, str) and "@" in x:
                _add(x.strip().lower())
            elif isinstance(x, dict):
                _add(_mailbox_address(x))

    if isinstance(payload, list):
        _from_list(payload)
        return found

    if not isinstance(payload, dict):
        return found

    for key in (
        "mailboxes",
        "addresses",
        "inboxes",
        "boxes",
        "emails",
        "accounts",
    ):
        _from_list(payload.get(key))

    data = payload.get("data")
    if isinstance(data, list):
        _from_list(data)
    elif isinstance(data, dict):
        for key in ("mailboxes", "addresses", "inboxes", "boxes", "emails", "accounts"):
            _from_list(data.get(key))

    # Derive from messages' To fields when no explicit mailbox list
    if not found:
        for it in extract_message_list(payload):
            to_raw = _pick(it, "to", "to_address", "toAddress", "recipient", "mailbox")
            if isinstance(to_raw, list):
                for t in to_raw:
                    if isinstance(t, str) and "@" in t:
                        _add(t.strip().lower())
                    elif isinstance(t, dict):
                        _add(_mailbox_address(t))
            elif isinstance(to_raw, dict):
                _add(_mailbox_address(to_raw))
            elif isinstance(to_raw, str) and "@" in to_raw:
                # may be "Name <a@b.com>" or comma-separated
                for part in re_split_emails(to_raw):
                    _add(part)

    return found


def re_split_emails(raw: str) -> list[str]:
    import re

    out: list[str] = []
    for m in re.finditer(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", raw or ""):
        out.append(m.group(0).lower())
    return out


def message_matches_mailbox(msg: Message, email: str) -> bool:
    """True if message is for this mailbox (To / explicit mailbox fields).

    When the upstream JSON has **no** To/recipient fields (legacy single-inbox
    API), keep all messages — filtering would empty the list incorrectly.
    """
    target = (email or "").strip().lower()
    if not target or target.startswith("api@"):
        return True  # API root row: keep all
    to_field = (msg.to or "").strip()
    # No recipient metadata → cannot filter; treat as match
    if not to_field:
        return True
    hay = to_field.lower()
    if target in hay:
        return True
    local = target.split("@", 1)[0]
    if local and len(local) >= 4 and local in hay:
        return True
    return False


class HttpApiProvider:
    name = "http_api"
    # Local date filter on normalized list (no universal server cursor)
    time_paging = "local_filter"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def can_handle(self, account: Any) -> bool:
        return _provider_value(account) == "http_api"

    def _get_client(self, *, proxy: str | None = None) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        # Manual redirects so we can SSRF-check each hop
        kwargs: dict[str, Any] = {"timeout": _TIMEOUT, "follow_redirects": False}
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs), True

    def fetch_url(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        proxy: str | None = None,
    ) -> httpx.Response:
        """GET/POST url with SSRF validation including redirects.

        SSRF: resolve DNS and block private/metadata IPs, then request the
        **original hostname** so TLS SNI works (Cloudflare / workers.dev reject
        bare-IP HTTPS with SSLV3_ALERT_HANDSHAKE_FAILURE when using pin-to-IP).

        Returns a streamed response opened via ``client.stream`` (caller must
        read/close the body). Body size limits are enforced by
        :func:`_read_limited` while streaming.
        """
        # Keep original URL for redirect base; re-validate each hop
        current_orig = validate_url(url)
        client, close = self._get_client(proxy=proxy)
        stream_cm = None
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                # Re-check DNS / private ranges every hop without rewriting host
                validate_url(current_orig, resolve_dns=True)
                req_headers = dict(headers or {})
                if _origin(current_orig) != _origin(url):
                    req_headers = {
                        k: v
                        for k, v in req_headers.items()
                        if k.lower() not in _SENSITIVE_REDIRECT_HEADERS
                    }
                # httpx >=0.28: use client.stream() so the body is not fully
                # buffered before our byte budget can run.
                stream_cm = client.stream(
                    method.upper(),
                    current_orig,
                    headers=req_headers,
                    follow_redirects=False,
                )
                resp = stream_cm.__enter__()

                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    try:
                        stream_cm.__exit__(None, None, None)
                    except Exception:
                        pass
                    stream_cm = None
                    if not loc:
                        raise SsrfError("Redirect without Location / 重定向缺少 Location")
                    # Validate redirect against original host URL as base
                    current_orig = validate_redirect_target(current_orig, loc)
                    if resp.status_code in (302, 303):
                        method = "GET"
                    continue
                # Stash stream context + optional owned client for _read_limited
                setattr(resp, "_openmail_stream_cm", stream_cm)
                if close:
                    setattr(resp, "_openmail_owned_client", client)
                return resp
            raise SsrfError("Too many redirects / 重定向过多")
        except Exception:
            if stream_cm is not None:
                try:
                    stream_cm.__exit__(None, None, None)
                except Exception:
                    pass
            if close:
                try:
                    client.close()
                except Exception:
                    pass
            raise


def _origin(url: str) -> tuple[str, str, int | None]:
    p = urlparse(url)
    port = p.port
    if port is None:
        port = 443 if p.scheme.lower() == "https" else 80
    return p.scheme.lower(), (p.hostname or "").lower(), port


def _read_limited(resp: httpx.Response) -> bytes:
    """Stream-read at most MAX_BODY_BYTES without trusting Content-Length.

    Expects a response opened via ``client.stream`` (see fetch_url). Closes the
    stream context and any client we own when finished.
    """
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                raise ValueError("上游响应过大 / Upstream response too large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        stream_cm = getattr(resp, "_openmail_stream_cm", None)
        if stream_cm is not None:
            try:
                stream_cm.__exit__(None, None, None)
            except Exception:
                pass
        else:
            try:
                resp.close()
            except Exception:
                pass
        owned = getattr(resp, "_openmail_owned_client", None)
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass


def _truthy_error_field(value: Any) -> bool:
    """True when an error/errors/exception field indicates a real failure.

    Null, empty string, empty list/dict, and False are treated as absent so
    success shapes like ``{"messages": [...], "error": null}`` still work.
    """
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, dict, tuple, set)) and len(value) == 0:
        return False
    return True


def _payload_is_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is False or payload.get("ok") is False:
        return True
    for key in ("error", "errors", "exception"):
        if key in payload and _truthy_error_field(payload.get(key)):
            return True
    return False


def _payload_has_supported_shape(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    if not isinstance(payload, dict) or _payload_is_error(payload):
        return False
    keys = {"messages", "mails", "items", "results", "parsed_mails", "emails", "data", "mailboxes", "addresses", "inboxes", "boxes", "accounts"}
    return bool(keys.intersection(payload)) or _looks_like_message(payload)


def _with_folder(url: str, folder: str) -> str:
    if not folder or folder == "inbox":
        return url
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q.setdefault("folder", folder)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))

def _fetch_impl(
    self: HttpApiProvider,
    account: Any,
    *,
    folder: str = "inbox",
    quick: bool = True,
    limits: dict[str, Any] | None = None,
    credentials: dict[str, Any] | None = None,
) -> FetchResult:
    _ = quick  # same path for now
    creds = dict(credentials or {})
    api_url = str(creds.get("api_url") or "").strip()
    if not api_url:
        return FetchResult(
            ok=False,
            folder=folder,
            error="缺少 api_url / Missing api_url",
        )

    extra_headers = build_api_auth_headers(creds)

    proxy = creds.get("proxy") or getattr(account, "proxy", None)
    proxy_str = str(proxy).strip() if proxy else None

    candidates = expand_api_url_candidates(api_url)
    last_err: str | None = None
    payload: Any = None
    used_url = api_url
    deadline = time.monotonic() + _CANDIDATE_DEADLINE

    for try_url in candidates:
        if time.monotonic() >= deadline:
            last_err = "候选 API 探测超时 / API candidate probe deadline exceeded"
            break
        resp: httpx.Response | None = None
        try:
            resp = self.fetch_url(
                _with_folder(try_url, folder), headers=extra_headers or None, proxy=proxy_str or None
            )
        except SsrfError as exc:
            last_err = f"SSRF 拦截: {exc.message}"
            # SSRF on one candidate is fatal for that host
            if try_url == candidates[0]:
                return FetchResult(ok=False, folder=folder, error=last_err)
            continue
        except httpx.TimeoutException:
            last_err = "取件超时，请重试 / Fetch timed out, please retry"
            continue
        except httpx.HTTPError as exc:
            last_err = f"网络错误 / Network error: {exc.__class__.__name__}"
            continue

        if resp.status_code >= 400:
            # 401/403 on a path often means "right host, need auth or wrong path"
            last_err = (
                f"上游 HTTP {resp.status_code} / Upstream HTTP {resp.status_code}"
            )
            # Drain/close streamed body so the connection is released
            try:
                _read_limited(resp)
            except Exception:
                try:
                    resp.close()
                except Exception:
                    pass
                owned = getattr(resp, "_openmail_owned_client", None)
                if owned is not None:
                    try:
                        owned.close()
                    except Exception:
                        pass
            # Keep trying other paths (root HTML 200 vs /api/mails 401 with key)
            if resp.status_code in (401, 403, 404):
                continue
            # 5xx: try next path once
            if resp.status_code >= 500:
                continue
            continue

        try:
            content = _read_limited(resp)
            payload = json.loads(content.decode("utf-8", errors="strict"))
        except ValueError as exc:
            last_err = str(exc) if "过大" in str(exc) else "上游返回非 JSON / Upstream returned non-JSON"
            continue

        # Valid JSON — prefer paths that yield messages or mailboxes
        if _payload_has_supported_shape(payload):
            # Empty list JSON is still success (no mail yet)
            used_url = try_url
            break
        last_err = "上游 JSON 无可解析邮件 / Upstream JSON has no messages"
        payload = None
    else:
        return FetchResult(
            ok=False,
            folder=folder,
            error=last_err
            or "无法从 Worker 根地址解析邮件 API，请检查密钥或 API 路径",
        )

    assert payload is not None
    mailbox_list = extract_mailbox_list(payload)
    items = extract_message_list(payload)
    top = int((limits or {}).get("top", 50) or (limits or {}).get("max_messages", 50) or 50)
    top = max(1, min(int(top), 100))

    # Filter to the concrete mailbox when account.email is a real address
    email_addr = (
        getattr(account, "email", None) or creds.get("email") or ""
    ).strip().lower()
    filter_email = email_addr if email_addr and not email_addr.startswith("api@") else ""

    # Pull a wider window when time-filtering so local since/before has room
    pull_top = top
    if limits and (
        limits.get("since")
        or limits.get("before")
        or limits.get("received_after")
        or limits.get("received_before")
    ):
        pull_top = max(top, min(100, top * 3))

    messages: list[Message] = []
    explicit_folder_seen = False
    for i, it in enumerate(items):
        folder_value = _pick(it, "folder", "mailbox_folder", "label", "category")
        if folder_value is not None:
            explicit_folder_seen = True
            if str(folder_value).strip().lower() != str(folder).strip().lower():
                continue
        msg = normalize_message_item(it, folder=folder, index=i)
        if filter_email and not message_matches_mailbox(msg, filter_email):
            continue
        messages.append(msg)
        if len(messages) >= pull_top:
            break

    if folder != "inbox" and items and not explicit_folder_seen:
        return FetchResult(
            ok=False,
            folder=folder,
            error="上游 API 未声明文件夹，无法安全获取该文件夹 / Upstream API does not support folder filtering",
        )

    if limits:
        from app.providers.base import filter_messages_by_time

        messages = filter_messages_by_time(
            messages,
            since=str(limits.get("since") or limits.get("received_after") or "") or None,
            before=str(limits.get("before") or limits.get("received_before") or "") or None,
        )
        messages = messages[:top]

    # If API only returned mailbox inventory (no messages yet), still ok
    meta: dict[str, Any] = {}
    if mailbox_list:
        meta["mailboxes"] = mailbox_list
        meta["mailbox_count"] = len(mailbox_list)
    if filter_email:
        meta["filter_email"] = filter_email
    if used_url and used_url.rstrip("/") != api_url.rstrip("/"):
        meta["resolved_api_url"] = used_url

    updates = CredentialUpdates(
        session_meta=meta or None,
        mailboxes=mailbox_list or None,
    )

    return FetchResult(
        ok=True,
        messages=messages,
        folder=folder,
        credential_updates=updates if updates.any() else None,
    )


def _health_impl(
    self: HttpApiProvider,
    account: Any,
    *,
    credentials: dict[str, Any] | None = None,
) -> HealthResult:
    _ = self, account
    creds = dict(credentials or {})
    api_url = str(creds.get("api_url") or "").strip()
    if not api_url:
        return HealthResult(ok=False, detail="missing api_url")
    try:
        validate_url(api_url)
    except SsrfError as exc:
        return HealthResult(ok=False, detail=exc.message)
    return HealthResult(ok=True, detail="api_url looks safe")


# Real methods on the class (not free-function monkey-patches) so inheritance
# and type checkers behave. Bodies stay module-level for testability.
HttpApiProvider.fetch = _fetch_impl  # type: ignore[method-assign]
HttpApiProvider.health = _health_impl  # type: ignore[method-assign]
