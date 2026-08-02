"""mail.com cookie-session provider.

Concepts from mail.com.helper (not a line-for-line port):
- try_restore: load cookies → GET lightmailer folder page → FolderListPage marker
- full_login: password form login when restore fails
- rolling cookie write-back via CredentialUpdates (no 6h hard-delete)
- fetch_message_list / fetch_detail for mailbox content

HTML/login endpoints may change; parse failures return "mail.com login parse failed".
Live network tests are opt-in (pytest mark network).
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from app.providers.base import CredentialUpdates, FetchResult, HealthResult, Message
from app.services.parser import attach_verification_code, extract_verification_code

# --- constants ----------------------------------------------------------------

DEFAULT_SITE = "mail.com"
MAIL_HOME_URL = "https://www.mail.com/"
LIGHT_FOLDER_URL = "https://lightmailer.mail.com/folderlist"
LIGHT_START_URL = "https://lightmailer.mail.com/start?device=desktop&ott={ott}"
# Keep single HTTP short — multi-step login × retries × WARP must stay under browser timeout
DEFAULT_TIMEOUT = 12.0
QUICK_LIMIT = 15
FULL_LIMIT = 50
# Cap parallel URL probes so a flaky mail.com path cannot burn 55s+
MAX_RESTORE_PROBES = 3
MAX_LOGIN_URL_PROBES = 2
MAX_FOLDER_PROBES = 3
MAX_DETAIL_HYDRATE = 2

# Session-valid markers (helper: FolderListPage)
SESSION_OK_MARKERS = (
    "FolderListPage",
    "folderlistpage",
    "data-webdriver=\"folder-list\"",
    "id=\"folderList\"",
    "mail-app-container",
    "nav-mailbox",
)

SESSION_LOSS_MARKERS = (
    "name=\"password\"",
    "id=\"login-button\"",
    "login.mail.com",
    "/login",
    "Please log in",
    "请登录",
)

# Lightmailer / webmail entry candidates (relative to site)
FOLDER_PATH_CANDIDATES = (
    "/mail",
    "/cgi-bin/login",
    "/lp/home",
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class _FormParser(HTMLParser):
    """Collect HTML forms and inputs for login POST reconstruction."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._cur = {
                "action": ad.get("action", ""),
                "method": (ad.get("method") or "get").lower(),
                "id": ad.get("id", ""),
                "name": ad.get("name", ""),
                "inputs": {},
            }
            self.forms.append(self._cur)
        elif tag in ("input", "button") and self._cur is not None:
            name = ad.get("name") or ""
            if not name and tag == "button":
                return
            itype = (ad.get("type") or "text").lower()
            value = ad.get("value") or ""
            if name:
                self._cur["inputs"][name] = {"type": itype, "value": value}
        elif tag == "select" and self._cur is not None:
            name = ad.get("name") or ""
            if name:
                self._cur["inputs"][name] = {"type": "select", "value": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._cur = None


def parse_forms(html: str) -> list[dict[str, Any]]:
    parser = _FormParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []
    return parser.forms


def pick_login_form(forms: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the password login form from parsed forms."""
    best: dict[str, Any] | None = None
    best_score = -1
    for form in forms:
        inputs = form.get("inputs") or {}
        names = {n.lower() for n in inputs}
        types = {v.get("type", "") for v in inputs.values()}
        score = 0
        if "password" in types:
            score += 5
        if any(n in names for n in ("password", "passwd", "pass", "login_password")):
            score += 3
        if any(
            n in names
            for n in (
                "username",
                "email",
                "login",
                "loginname",
                "user",
                "userid",
                "identifier",
            )
        ):
            score += 2
        if "login" in (form.get("id") or "").lower() or "login" in (form.get("name") or "").lower():
            score += 2
        if "login" in (form.get("action") or "").lower():
            score += 1
        if score > best_score:
            best_score = score
            best = form
    if best is None or best_score < 5:
        return None
    return best


def session_looks_valid(html: str) -> bool:
    if not html:
        return False
    lower = html
    # Case-sensitive FolderListPage first (helper marker)
    if "FolderListPage" in html:
        return True
    low = lower.lower()
    if any(m.lower() in low for m in SESSION_OK_MARKERS):
        # Avoid false positive on login page that mentions mailbox marketing
        if _looks_like_login_page(html):
            return False
        return True
    return False


def _looks_like_login_page(html: str) -> bool:
    low = (html or "").lower()
    has_password = 'type="password"' in low or "type='password'" in low
    if not has_password:
        return False
    return any(m.lower() in low for m in ("login", "sign in", "anmelden", "密码"))


def _looks_like_session_loss(html: str) -> bool:
    if not html:
        return True
    if session_looks_valid(html):
        return False
    return _looks_like_login_page(html)


def cookies_to_jar_list(cookies: list[dict[str, Any]] | dict[str, str] | None) -> list[dict[str, Any]]:
    """Normalize cookies into a list of dicts suitable for httpx / storage."""
    if not cookies:
        return []
    if isinstance(cookies, dict):
        return [{"name": k, "value": str(v), "domain": "", "path": "/"} for k, v in cookies.items()]
    out: list[dict[str, Any]] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("Name")
        value = c.get("value") if "value" in c else c.get("Value")
        if name is None or value is None:
            continue
        out.append(
            {
                "name": str(name),
                "value": str(value),
                "domain": str(c.get("domain") or c.get("Domain") or ""),
                "path": str(c.get("path") or c.get("Path") or "/"),
                "secure": bool(c.get("secure", c.get("Secure", False))),
                "httpOnly": bool(c.get("httpOnly", c.get("http_only", c.get("HttpOnly", False)))),
            }
        )
    return out


def dump_client_cookies(client: Any) -> list[dict[str, Any]]:
    """Export httpx/curl_cffi cookie jar to list[dict]."""
    out: list[dict[str, Any]] = []
    jar = getattr(client, "cookies", None)
    if jar is None:
        return out
    # httpx.Cookies supports .jar (CookieJar) or iteration
    try:
        for cookie in jar.jar:  # type: ignore[attr-defined]
            out.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                    "secure": bool(getattr(cookie, "secure", False)),
                    "httpOnly": bool(getattr(cookie, "rest", {}).get("HttpOnly", False))
                    if isinstance(getattr(cookie, "rest", None), dict)
                    else False,
                }
            )
        if out:
            return out
    except Exception:
        pass
    try:
        # Mapping-like
        for name, value in jar.items():
            out.append({"name": str(name), "value": str(value), "domain": "", "path": "/"})
    except Exception:
        pass
    return out


def apply_cookies(client: Any, cookies: list[dict[str, Any]]) -> None:
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name is None or value is None:
            continue
        kwargs: dict[str, Any] = {}
        if c.get("domain"):
            kwargs["domain"] = c["domain"]
        if c.get("path"):
            kwargs["path"] = c["path"]
        try:
            client.cookies.set(name, value, **kwargs)
        except TypeError:
            try:
                client.cookies.set(name, value)
            except Exception:
                pass
        except Exception:
            pass


def _site_base(site: str) -> str:
    site = (site or DEFAULT_SITE).strip()
    if site.startswith("http://") or site.startswith("https://"):
        return site.rstrip("/")
    return f"https://www.{site.lstrip('.')}"


def _login_urls(site: str) -> list[str]:
    """Few high-value login entry points only (Path B fallback)."""
    base = _site_base(site)
    host = urlparse(base).hostname or "www.mail.com"
    bare = host[4:] if host.startswith("www.") else host
    # Prefer real SSO host; www/login roots historically redirected / 403
    urls = [
        f"https://login.{bare}/login",
        f"{base}/",
        f"https://www.{bare}/login",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:MAX_LOGIN_URL_PROBES]


def _folder_urls(site: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Prefer lightmailer folderlist (mail.com.helper), then a few webmail paths."""
    urls: list[str] = []
    if meta:
        for key in ("folder_url", "mailbox_url", "lightmailer_url", "start_url"):
            if meta.get(key):
                urls.append(str(meta[key]))
    # Helper canonical entry
    if LIGHT_FOLDER_URL not in urls:
        urls.insert(0, LIGHT_FOLDER_URL)
    base = _site_base(site)
    host = urlparse(base).hostname or "www.mail.com"
    bare = host[4:] if host.startswith("www.") else host
    candidates = [
        f"https://lightmailer.{bare}/folderlist",
        f"https://www.{bare}/mail",
        f"{base}/mail",
    ]
    for u in candidates:
        if u not in urls:
            urls.append(u)
    return urls[: max(MAX_FOLDER_PROBES, 1) + (1 if meta and meta.get("folder_url") else 0)]


def extract_ott(url: str, page_html: str) -> str:
    """Extract lightmailer one-time token from redirect URL or HTML (mail.com.helper).

    Strip URL fragments first — form actions like ``login#.7518-header`` must not
    leak into the token (would break lightmailer start).
    """
    clean_url = (url or "").split("#", 1)[0]
    match = re.search(r"[?&]ott=([0-9a-fA-F-]{8,})", clean_url) or re.search(
        r"[?&]ott=([0-9a-fA-F-]{8,})", page_html or ""
    ) or re.search(r"ott=([0-9a-fA-F-]{8,})", page_html or "")
    if not match:
        raise RuntimeError(
            "mail.com 未返回 lightmailer 登录令牌 (ott)。可能需要验证码、额外验证或浏览器 Cookie。"
        )
    return match.group(1).split("#", 1)[0]


def extract_wicket_redirect(xml_text: str) -> str:
    match = re.search(r"<redirect><!\[CDATA\[(.*?)\]\]></redirect>", xml_text or "")
    if not match:
        # sometimes without CDATA
        match = re.search(r"<redirect>(.*?)</redirect>", xml_text or "", re.I | re.S)
    if not match:
        raise RuntimeError("mail.com lightmailer 未返回启动跳转。")
    return match.group(1).strip()


# Avoid bare "wrong"/"denied" — marketing / cookie banners false-positive as bad password.
_BAD_CREDENTIAL_RE = re.compile(
    r"(invalid\s+password|incorrect\s+password|wrong\s+password|"
    r"invalid\s+(email|login|credentials|user)|"
    r"login\s+failed|authentication\s+failed|"
    r"账号或密码错误|密码错误|用户名或密码|凭证无效)",
    re.I,
)

_RATE_LIMIT_RE = re.compile(
    r"(too\s+many\s+requests|rate\s*limit|try\s+again\s+later|"
    r"captcha|recaptcha|hcaptcha|challenge|"
    r"unusual\s+activity|suspicious|"
    r"访问过于频繁|请稍后再试|验证码)",
    re.I,
)


def html_indicates_bad_credentials(html: str | None) -> bool:
    """True only when the page clearly reports bad password/login — not marketing copy."""
    if not html:
        return False
    if html_indicates_rate_limit(html):
        return False
    return bool(_BAD_CREDENTIAL_RE.search(html))


def html_indicates_rate_limit(html: str | None) -> bool:
    if not html:
        return False
    return bool(_RATE_LIMIT_RE.search(html))


def is_transient_login_error(err: str | None) -> bool:
    """Errors that often succeed on retry (parse/network/ott/rate-limit)."""
    if not err:
        return True
    # "账号或密码错误" may still be a false positive under flaky SSO — treat as retryable
    # when we have multi-attempt outer loop; final message is decided by caller.
    low = err.lower()
    markers = (
        "parse failed",
        "login parse",
        "ott",
        "timeout",
        "network",
        "连接",
        "请求失败",
        "未返回",
        "session",
        "登录失败",
        "login failed",
        "频繁",
        "稍后",
        "captcha",
        "rate",
        "账号或密码错误",
    )
    return any(m in low or m in err for m in markers)


def parse_login_form_helper(home_html: str) -> tuple[str, dict[str, str]]:
    """Parse login form whose action contains login.mail.com/login (helper style)."""
    parser = _FormParser()
    parser.feed(home_html or "")
    for form in parser.forms:
        action = form.get("action") or ""
        inputs = form.get("inputs") or {}
        has_pass = any("pass" in n.lower() or (i.get("type") or "").lower() == "password" for n, i in inputs.items())
        if not has_pass:
            continue
        # Helper: only the real SSO form action (avoid matching random /login links)
        if "login.mail.com/login" in action:
            fields = {n: (info.get("value") or "") for n, info in inputs.items()}
            if any("pass" in n.lower() or (info.get("type") or "").lower()=="password" for n, info in inputs.items()):
                return action, fields
    form = pick_login_form(parser.forms)
    if form:
        inputs = form.get("inputs") or {}
        fields = {n: (i.get("value") or "") for n, i in inputs.items()}
        if any("pass" in n.lower() or (i.get("type") or "").lower() == "password" for n, i in inputs.items()):
            return form.get("action") or "", fields
    raise RuntimeError("未找到 mail.com 登录表单。")


def parse_lightmailer_message_list(listing_url: str, listing_html: str, *, limit: int, folder: str) -> list[Message]:
    """Parse lightmailer MessageListPage (mail.com.helper heuristics)."""
    import html as html_mod
    messages: list[Message] = []
    starts = [m.start() for m in re.finditer(r'<li class="message-list__item\b', listing_html or "", re.I)]
    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(listing_html)
        blocks.append(listing_html[start:end])

    if not blocks:
        # fallback generic parser
        return parse_message_list_html(listing_html, limit=limit, folder=folder)

    for block in blocks:
        if len(messages) >= limit:
            break
        href_match = re.search(r'href="(\./messagedetail\?[^"]+)"', block)
        if not href_match:
            href_match = re.search(r'href="(messagedetail\?[^"]+)"', block)
        if not href_match:
            continue
        detail_rel = html_mod.unescape(href_match.group(1))
        detail_url = urljoin(listing_url, detail_rel)
        mail_id = ""
        mid = re.search(r"[?&]mailId=(\d+)", detail_rel)
        if mid:
            mail_id = mid.group(1)
        subject = ""
        sm = re.search(r'class="mail-header__subject"[^>]*>(.*?)</dd>', block, re.I | re.S)
        if sm:
            subject = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sm.group(1))).strip()
        if not subject:
            om = re.search(r"Open E-mail:\s*(.*?)\s*</span>", block, re.I | re.S)
            if om:
                subject = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", om.group(1))).strip()
        sender = ""
        st = re.search(r'class="mail-header__sender"[^>]*title="([^"]*)"', block, re.I)
        if st:
            sender = html_mod.unescape(st.group(1)).strip()
        else:
            sm2 = re.search(r'class="mail-header__sender"[^>]*>(.*?)</dd>', block, re.I | re.S)
            if sm2:
                sender = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sm2.group(1))).strip()
        date = ""
        dm = re.search(r"Received\s+([^<]+)", block, re.I)
        if dm:
            date = dm.group(1).strip()
        msg = Message(
            id=mail_id or detail_url,
            subject=subject,
            from_=sender,
            from_address=sender,
            date=date or None,
            body_preview="",
            folder=folder,
            raw_refs={"detail_url": detail_url, "mail_id": mail_id},
        )
        messages.append(msg)
    return messages


def _http_client(timeout: float, proxy: str | None = None):
    """Prefer curl_cffi for TLS fingerprint; fall back to httpx."""
    proxies = proxy or None
    try:
        from curl_cffi import requests as curl_requests  # type: ignore

        session = curl_requests.Session(impersonate="chrome")
        session.timeout = timeout
        if proxies:
            session.proxies = {"http": proxies, "https": proxies}
        session.headers.update({"User-Agent": USER_AGENT})
        return _CurlClientAdapter(session, timeout=timeout)
    except Exception:
        pass

    import httpx

    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    }
    if proxies:
        kwargs["proxy"] = proxies
    return httpx.Client(**kwargs)


class _CurlClientAdapter:
    """Minimal adapter so curl_cffi Session looks like httpx Client."""

    def __init__(self, session: Any, *, timeout: float) -> None:
        self._s = session
        self.timeout = timeout
        self.cookies = session.cookies

    def get(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self._s.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self._s.post(url, **kwargs)

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def _resp_text(resp: Any) -> str:
    text = getattr(resp, "text", None)
    if text is not None:
        return text
    content = getattr(resp, "content", b"") or b""
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _resp_status(resp: Any) -> int:
    return int(getattr(resp, "status_code", getattr(resp, "status", 0)) or 0)


def parse_message_list_html(html: str, *, limit: int = 50, folder: str = "inbox") -> list[Message]:
    """Best-effort extract of message rows from lightmailer HTML / embedded JSON."""
    by_id: dict[str, Message] = {}
    order: list[str] = []

    def _add(msg: Message) -> None:
        if not msg.id:
            return
        if msg.id in by_id:
            # Prefer richer fields from later patterns
            prev = by_id[msg.id]
            if msg.subject and not prev.subject:
                prev.subject = msg.subject
            if msg.from_ and not prev.from_:
                prev.from_ = msg.from_
            if msg.from_address and not prev.from_address:
                prev.from_address = msg.from_address
            if msg.date and not prev.date:
                prev.date = msg.date
            if msg.body_preview and (
                not prev.body_preview or len(msg.body_preview) > len(prev.body_preview)
            ):
                prev.body_preview = msg.body_preview
            return
        by_id[msg.id] = msg
        order.append(msg.id)

    if not html:
        return []

    # Pattern B first: structured mail-item blocks (richest fixture format)
    block_re = re.compile(
        r'<div[^>]+class=["\'][^"\']*mail-item[^"\']*["\'][^>]*'
        r'data-id=["\']([^"\']+)["\'][^>]*>'
        r'.*?<span[^>]+class=["\']subject["\'][^>]*>([^<]*)</span>'
        r'(?:.*?<span[^>]+class=["\']from["\'][^>]*>([^<]*)</span>)?'
        r'(?:.*?<span[^>]+class=["\']date["\'][^>]*>([^<]*)</span>)?',
        re.IGNORECASE | re.DOTALL,
    )
    for m in block_re.finditer(html):
        mid, subj, frm, date = m.group(1), m.group(2), m.group(3) or "", m.group(4)
        from_disp = frm.strip()
        addr_m = re.search(r"[\w.+-]+@[\w.-]+", from_disp)
        _add(
            Message(
                id=mid.strip(),
                subject=subj.strip(),
                from_=from_disp,
                from_address=(addr_m.group(0).lower() if addr_m else ""),
                date=date.strip() if date else None,
                body_preview=subj.strip()[:280],
                folder=folder,
            )
        )
        if len(order) >= limit:
            return [by_id[i] for i in order[:limit]]

    # Pattern A: data-mail-id / data-id rows
    row_re = re.compile(
        r'data-(?:mail-)?id=["\']([^"\']+)["\'][^>]*>'
        r'.*?(?:class=["\'][^"\']*subject[^"\']*["\'][^>]*>([^<]*)|'
        r'data-subject=["\']([^"\']*)["\'])',
        re.IGNORECASE | re.DOTALL,
    )
    for m in row_re.finditer(html):
        mid = m.group(1)
        subj = (m.group(2) or m.group(3) or "").strip()
        _add(Message(id=mid, subject=subj, folder=folder, body_preview=subj[:280]))
        if len(order) >= limit:
            return [by_id[i] for i in order[:limit]]

    # Pattern C: JSON-ish "id":"...","subject":"..."
    if not order:
        json_re = re.compile(
            r'"id"\s*:\s*"([^"]+)"\s*,\s*"subject"\s*:\s*"([^"]*)"',
            re.IGNORECASE,
        )
        for m in json_re.finditer(html):
            _add(
                Message(
                    id=m.group(1),
                    subject=m.group(2),
                    folder=folder,
                    body_preview=m.group(2)[:280],
                )
            )
            if len(order) >= limit:
                break

    return [by_id[i] for i in order[:limit]]


def parse_message_detail_html(html: str, *, msg_id: str, folder: str = "inbox") -> Message:
    """Extract subject/from/body from a detail page or fixture."""
    subject = ""
    from_ = ""
    body_text = ""
    body_html = ""

    sm = re.search(
        r'<h1[^>]*class=["\'][^"\']*subject[^"\']*["\'][^>]*>(.*?)</h1>',
        html or "",
        re.I | re.S,
    )
    if sm:
        subject = re.sub(r"<[^>]+>", "", sm.group(1)).strip()
    if not subject:
        sm = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
        if sm:
            subject = re.sub(r"<[^>]+>", "", sm.group(1)).strip()

    fm = re.search(
        r'<[^>]+class=["\'][^"\']*from[^"\']*["\'][^>]*>(.*?)</',
        html or "",
        re.I | re.S,
    )
    if fm:
        from_ = re.sub(r"<[^>]+>", "", fm.group(1)).strip()

    bm = re.search(
        r'<div[^>]+class=["\'][^"\']*mail-body[^"\']*["\'][^>]*>(.*?)</div>',
        html or "",
        re.I | re.S,
    )
    if bm:
        body_html = bm.group(1).strip()
        body_text = re.sub(r"<[^>]+>", " ", body_html)
        body_text = re.sub(r"\s+", " ", body_text).strip()
    else:
        # plain text fixture
        pm = re.search(
            r'<pre[^>]+class=["\'][^"\']*body-text[^"\']*["\'][^>]*>(.*?)</pre>',
            html or "",
            re.I | re.S,
        )
        if pm:
            body_text = pm.group(1).strip()

    addr_m = re.search(r"[\w.+-]+@[\w.-]+", from_)
    msg = Message(
        id=msg_id,
        subject=subject,
        from_=from_,
        from_address=(addr_m.group(0).lower() if addr_m else ""),
        body_text=body_text,
        body_html=body_html,
        body_preview=(body_text or subject)[:280],
        folder=folder,
        verification_code=extract_verification_code(
            subject=subject, body_text=body_text, body_html=body_html
        ),
    )
    return msg


class MailcomCookieProvider:
    """Cookie-class provider specialized for mail.com / lightmailer sites."""

    name = "cookie"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def can_handle(self, account: Any) -> bool:
        p = getattr(account, "provider", None)
        if p is not None and str(getattr(p, "value", p)) == "cookie":
            return True
        # Domain hint when provider unset/unknown
        email = (getattr(account, "email", None) or "").lower()
        if email.endswith("@mail.com") or email.endswith(".mail.com"):
            return True
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
        creds = dict(credentials or {})
        email_addr = (getattr(account, "email", None) or creds.get("email") or "").strip()
        password = creds.get("password") or getattr(account, "password", None) or ""
        site = str(creds.get("site") or DEFAULT_SITE)
        cookies = cookies_to_jar_list(
            creds.get("cookies") or creds.get("session_cookies") or creds.get("session")
        )
        meta = dict(creds.get("session_meta") or {})
        proxy = creds.get("proxy") or getattr(account, "proxy", None)

        if not email_addr:
            return FetchResult(ok=False, folder=folder, error="缺少邮箱地址")

        limit = QUICK_LIMIT if quick else FULL_LIMIT
        if limits and "max_messages" in limits:
            try:
                limit = max(1, min(int(limits["max_messages"]), 100))
            except (TypeError, ValueError):
                pass

        client = None
        try:
            client = _http_client(self.timeout, proxy=proxy if isinstance(proxy, str) else None)
            session_restored = False
            login_error: str | None = None

            if cookies:
                ok, meta_update = self.try_restore(client, cookies, site=site, meta=meta)
                if ok:
                    session_restored = True
                    if meta_update:
                        meta.update(meta_update)
                else:
                    # clear stale cookies in client and fall through to login
                    try:
                        client.cookies.clear()
                    except Exception:
                        pass

            if not session_restored:
                if not password:
                    return FetchResult(
                        ok=False,
                        folder=folder,
                        error="会话失效，请补充密码后重试",
                        session_restored=False,
                    )
                # One clean login per egress; outer loop may try another WARP.
                # Multi-attempt login here × multi-proxy was the main 499 timeout source.
                max_login_attempts = 1
                ok = False
                login_error = None
                meta_update = None
                last_errors: list[str] = []
                for attempt in range(max_login_attempts):
                    if attempt > 0:
                        try:
                            client.cookies.clear()
                        except Exception:
                            pass
                        time.sleep(0.4 * attempt)
                    ok, login_error, meta_update = self.full_login(
                        client, email_addr, str(password), site=site
                    )
                    if ok:
                        break
                    if login_error:
                        last_errors.append(login_error)
                if not ok:
                    final_err = login_error or "mail.com 登录失败"
                    if final_err == "账号或密码错误" and any(
                        is_transient_login_error(e) for e in last_errors
                    ):
                        final_err = (
                            "mail.com 登录不稳定（会话/页面解析失败），请稍后重试；"
                            "若持续失败再核对密码"
                        )
                    return FetchResult(
                        ok=False,
                        folder=folder,
                        error=final_err,
                        session_restored=False,
                    )
                if meta_update:
                    meta.update(meta_update)

            messages = self.fetch_message_list(
                client, folder=folder, limit=limit, site=site, meta=meta
            )
            # Hydrate only a couple bodies for verification codes (each is extra RTT)
            hydrate_n = MAX_DETAIL_HYDRATE if quick else min(5, limit)
            for i, msg in enumerate(list(messages)):
                if i >= hydrate_n:
                    break
                if msg.body_text or msg.body_html or msg.verification_code:
                    continue
                try:
                    detail = self.fetch_detail(
                        client, msg.id, folder=folder, site=site, meta=meta
                    )
                    if detail:
                        msg.subject = msg.subject or detail.subject
                        msg.from_ = msg.from_ or detail.from_
                        msg.from_address = msg.from_address or detail.from_address
                        msg.body_text = detail.body_text
                        msg.body_html = detail.body_html
                        msg.body_preview = detail.body_preview or msg.body_preview
                        msg.verification_code = detail.verification_code
                except Exception:
                    continue

            attach_verification_code(messages)
            cookie_dump = dump_client_cookies(client)
            return FetchResult(
                ok=True,
                messages=messages,
                folder=folder.lower(),
                session_restored=session_restored,
                credential_updates=CredentialUpdates(
                    session_cookies=cookie_dump,
                    session_meta=meta or None,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                ok=False,
                folder=folder,
                error=f"mail.com 取信失败: {exc}",
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        creds = dict(credentials or {})
        cookies = cookies_to_jar_list(
            creds.get("cookies") or creds.get("session_cookies")
        )
        site = str(creds.get("site") or DEFAULT_SITE)
        proxy = creds.get("proxy") or getattr(account, "proxy", None)
        client = None
        try:
            client = _http_client(self.timeout, proxy=proxy if isinstance(proxy, str) else None)
            if cookies:
                ok, _ = self.try_restore(client, cookies, site=site, meta=creds.get("session_meta"))
                if ok:
                    return HealthResult(ok=True, detail="会话有效")
            password = creds.get("password") or ""
            if password:
                email_addr = getattr(account, "email", None) or creds.get("email") or ""
                ok, err, _ = self.full_login(client, str(email_addr), str(password), site=site)
                if ok:
                    return HealthResult(ok=True, detail="登录成功")
                return HealthResult(ok=False, detail=err or "登录失败")
            return HealthResult(ok=False, detail="无有效会话")
        except Exception as exc:  # noqa: BLE001
            return HealthResult(ok=False, detail=str(exc))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # --- core steps (unit-testable with fixtures / mocked client) ---------------



    def try_restore(
        self,
        client: Any,
        cookies: list[dict[str, Any]],
        *,
        site: str = DEFAULT_SITE,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Load cookies and probe mailbox (lightmailer preferred, webmail fallback)."""
        apply_cookies(client, cookies)
        meta_update: dict[str, Any] = {}
        urls = _folder_urls(site, meta)[:MAX_RESTORE_PROBES]
        # Put lightmailer first but do not treat non-FolderList as hard fail for other URLs
        for url in urls:
            try:
                resp = client.get(url)
            except Exception:
                continue
            html = _resp_text(resp)
            status = _resp_status(resp)
            if status >= 400:
                continue
            is_light = "lightmailer" in (url or "").lower()
            if is_light:
                if "FolderListPage" in html:
                    meta_update["folder_url"] = str(getattr(resp, "url", url))
                    meta_update["last_probe"] = "restore_ok"
                    return True, meta_update
                # lightmailer without FolderListPage → try next candidate
                continue
            if session_looks_valid(html):
                meta_update["folder_url"] = str(getattr(resp, "url", url))
                meta_update["last_probe"] = "restore_ok"
                return True, meta_update
            if _looks_like_session_loss(html):
                # keep trying other URLs; only fail if all fail
                continue
        # If any probe hit explicit login page only, treat as failed session
        return False, None



    def full_login(
        self,
        client: Any,
        email_addr: str,
        password: str,
        *,
        site: str = DEFAULT_SITE,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Hybrid login: lightmailer path (helper) with webmail form fallback for fixtures/legacy."""
        # --- Path A: www.mail.com home → login.mail.com form → ott → lightmailer (helper) ---
        try:
            home = client.get(MAIL_HOME_URL)
            home_html = _resp_text(home)
            if not re.search(r'type\s*=\s*["\']?password["\']?', home_html or "", re.I):
                raise RuntimeError("home page has no password form")
            action, fields = parse_login_form_helper(home_html)
            fields = dict(fields)
            if "login.mail.com" not in (action or ""):
                # Not the real SSO form — use Path B (login URL candidates / fixtures)
                raise RuntimeError("not sso login form")
            if not any("pass" in k.lower() for k in fields):
                raise RuntimeError("home form missing password")
            fields["username"] = email_addr
            fields["password"] = password
            # also fill common aliases present in form
            for k in list(fields.keys()):
                lk = k.lower()
                if lk in ("email", "login", "loginname", "user", "userid", "identifier"):
                    fields[k] = email_addr
                if "pass" in lk:
                    fields[k] = password
            # Fragment on form action (e.g. #.7518-header-login1-1) is for analytics;
            # some HTTP clients mishandle it — always strip before POST.
            post_url = urljoin(str(getattr(home, "url", MAIL_HOME_URL)), action).split("#", 1)[0]
            login = client.post(
                post_url,
                data=fields,
                headers={
                    "Referer": str(getattr(home, "url", MAIL_HOME_URL)),
                    "Origin": "https://www.mail.com",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            login_url = str(getattr(login, "url", post_url)).split("#", 1)[0]
            login_html = _resp_text(login)

            if "FolderListPage" in login_html or session_looks_valid(login_html):
                return True, None, {"folder_url": login_url, "last_probe": "login_ok"}

            try:
                ott = extract_ott(login_url, login_html)
                light = client.get(LIGHT_START_URL.format(ott=ott))
                light_url = str(getattr(light, "url", LIGHT_START_URL))
                light_html = _resp_text(light)
                ajax_headers = {
                    "Wicket-Ajax": "true",
                    "Wicket-Ajax-BaseURL": "start?0&device=desktop",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/xml, text/xml, */*; q=0.01",
                    "Referer": light_url,
                }
                startup = client.get(
                    urljoin(light_url, "./start?0-1.0-&device=desktop"),
                    headers=ajax_headers,
                )
                startup_text = _resp_text(startup)
                try:
                    redirect = extract_wicket_redirect(startup_text)
                    folder = client.get(urljoin(light_url, redirect))
                    folder_html = _resp_text(folder)
                    folder_url = str(getattr(folder, "url", light_url))
                    if "FolderListPage" in folder_html or session_looks_valid(folder_html):
                        return True, None, {
                            "folder_url": folder_url,
                            "light_url": light_url,
                            "last_probe": "login_ok",
                        }
                except Exception:
                    if "FolderListPage" in light_html or "FolderListPage" in startup_text:
                        return True, None, {
                            "folder_url": light_url,
                            "last_probe": "login_light_ok",
                        }
            except Exception:
                # no ott — fall through to path B using cookies from login response
                ok, meta = self.try_restore(
                    client, dump_client_cookies(client), site=site, meta=None
                )
                if ok:
                    return True, None, meta
                if html_indicates_rate_limit(login_html):
                    # Do not hard-fail — Path B / outer retry may still succeed
                    pass
                # Never hard-return "bad password" from Path A alone: fall through to Path B
        except Exception:
            pass

        # --- Path B: generic /login form pages (unit fixtures + alternate portals) ---
        last_err = "mail.com login parse failed"
        saw_clear_bad_password = False
        saw_rate_limit = False
        for login_url in _login_urls(site)[:MAX_LOGIN_URL_PROBES]:
            try:
                resp = client.get(login_url)
            except Exception as exc:
                last_err = f"登录页请求失败: {exc}"
                continue
            html = _resp_text(resp)
            forms = parse_forms(html)
            form = pick_login_form(forms)
            if form is None:
                if html_indicates_rate_limit(html):
                    saw_rate_limit = True
                    last_err = "mail.com 访问过于频繁或需要验证码，请稍后重试"
                elif not html or "password" not in html.lower():
                    last_err = "mail.com login parse failed"
                continue
            action = form.get("action") or login_url
            post_url = urljoin(str(getattr(resp, "url", login_url)), action)
            payload: dict[str, str] = {}
            user_field = None
            pass_field = None
            for name, info in (form.get("inputs") or {}).items():
                itype = (info.get("type") or "").lower()
                val = info.get("value") or ""
                lname = name.lower()
                if itype == "password" or "pass" in lname:
                    pass_field = name
                    payload[name] = password
                elif itype in ("submit", "button", "image"):
                    if val:
                        payload[name] = val
                elif lname in (
                    "username", "email", "login", "loginname", "user", "userid", "identifier",
                ) or itype in ("email", "text"):
                    if user_field is None or lname in ("username", "email", "login", "loginname"):
                        user_field = name
                    payload[name] = val
                else:
                    payload[name] = val
            if user_field:
                payload[user_field] = email_addr
            else:
                payload.setdefault("username", email_addr)
            if pass_field:
                payload[pass_field] = password
            else:
                payload.setdefault("password", password)
            try:
                post_resp = client.post(post_url, data=payload)
            except Exception as exc:
                last_err = f"登录提交失败: {exc}"
                continue
            post_html = _resp_text(post_resp)
            if session_looks_valid(post_html) or "FolderListPage" in post_html:
                return True, None, {
                    "folder_url": str(getattr(post_resp, "url", post_url)),
                    "last_probe": "login_ok",
                }
            ok, meta_update = self.try_restore(
                client, dump_client_cookies(client), site=site, meta=None
            )
            if ok:
                return True, None, meta_update
            if html_indicates_rate_limit(post_html):
                saw_rate_limit = True
                last_err = "mail.com 访问过于频繁或需要验证码，请稍后重试"
                continue
            if html_indicates_bad_credentials(post_html):
                # Keep trying other login URLs; only hard-fail after all exhausted
                saw_clear_bad_password = True
                last_err = "账号或密码错误"
                continue
            last_err = "mail.com login parse failed"
        if saw_rate_limit:
            return False, "mail.com 访问过于频繁或需要验证码，请稍后重试", None
        if saw_clear_bad_password and last_err == "账号或密码错误":
            return False, "账号或密码错误", None
        return False, last_err, None



    def fetch_message_list(
        self,
        client: Any,
        *,
        folder: str = "inbox",
        limit: int = 50,
        site: str = DEFAULT_SITE,
        meta: dict[str, Any] | None = None,
    ) -> list[Message]:
        """List messages: lightmailer messagelist when available, else generic HTML parse."""
        folder_l = (folder or "inbox").lower()
        meta = dict(meta or {})
        folder_url = meta.get("folder_url")

        pages: list[tuple[str, str]] = []
        # Probe known URLs including meta folder_url and /mail fixtures
        candidates = []
        if folder_url:
            candidates.append(str(folder_url))
        for u in _folder_urls(site, meta):
            if u not in candidates:
                candidates.append(u)
        candidates = candidates[:MAX_FOLDER_PROBES]
        for url in candidates:
            try:
                resp = client.get(url)
            except Exception:
                continue
            html = _resp_text(resp)
            final_url = str(getattr(resp, "url", url))
            pages.append((final_url, html))

            # lightmailer folder → find messagelist link
            if "FolderListPage" in html or "messagelist" in html.lower():
                patterns = []
                if folder_l in ("junk", "spam", "junkemail"):
                    patterns = [
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*data-webdriver="(?:SPAM|JUNK)[^"]*"',
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*>\s*(?:Spam|Junk)\s*<',
                    ]
                elif folder_l in ("sent", "sentitems", "sent mail"):
                    patterns = [
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*data-webdriver="(?:SENT|OUTBOX)[^"]*"',
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*>\s*(?:Sent|已发送)\s*<',
                        r'data-webdriver="SENT[^"]*"[^>]*href="(\./messagelist\?folderId=[^"]+)"',
                    ]
                else:
                    patterns = [
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*data-webdriver="INBOX:[^"]*"',
                        r'data-webdriver="INBOX:[^"]*"[^>]*href="(\./messagelist\?folderId=[^"]+)"',
                        r'href="(\./messagelist\?folderId=[^"]+)"[^>]*>\s*INBOX\s*<',
                    ]
                list_url = None
                for pattern in patterns:
                    match = re.search(pattern, html, re.I)
                    if match:
                        list_url = urljoin(final_url, match.group(1).replace("&amp;", "&"))
                        break
                if not list_url:
                    m = re.search(r'href="(\./messagelist\?[^"]+)"', html, re.I)
                    if m:
                        list_url = urljoin(final_url, m.group(1).replace("&amp;", "&"))
                if list_url:
                    try:
                        listing = client.get(list_url)
                        listing_html = _resp_text(listing)
                        listing_url = str(getattr(listing, "url", list_url))
                        msgs = parse_lightmailer_message_list(
                            listing_url, listing_html, limit=limit, folder=folder_l
                        )
                        if msgs:
                            return msgs
                    except Exception:
                        pass

            if "message-list__item" in html:
                msgs = parse_lightmailer_message_list(final_url, html, limit=limit, folder=folder_l)
                if msgs:
                    return msgs
            msgs = parse_message_list_html(html, limit=limit, folder=folder_l)
            if msgs:
                return msgs

        # empty but valid session
        for _, html in pages:
            if session_looks_valid(html) or "FolderListPage" in html:
                return []
        # if all probes look like login, surface session error
        if pages and all(_looks_like_session_loss(h) for _, h in pages):
            raise RuntimeError("会话已失效")
        return []


    def fetch_detail(
        self,
        client: Any,
        message_id: str,
        *,
        folder: str = "inbox",
        site: str = DEFAULT_SITE,
        meta: dict[str, Any] | None = None,
    ) -> Message | None:
        """Fetch a single message body by id (best-effort URL patterns)."""
        if not message_id:
            return None
        bases = _folder_urls(site, meta)
        candidates: list[str] = []
        for base in bases:
            b = base.rstrip("/")
            candidates.extend(
                [
                    f"{b}/mail/show/{message_id}",
                    f"{b}/message/{message_id}",
                    f"{b}/?msg={message_id}",
                    f"{b}/mail?id={message_id}",
                ]
            )
        for url in candidates:
            try:
                resp = client.get(url)
            except Exception:
                continue
            html = _resp_text(resp)
            if not html or _resp_status(resp) >= 400:
                continue
            if _looks_like_session_loss(html) and not session_looks_valid(html):
                continue
            msg = parse_message_detail_html(html, msg_id=message_id, folder=folder.lower())
            if msg.subject or msg.body_text or msg.body_html:
                return msg
        return None


# Back-compat alias used by registry
CookieMailcomProvider = MailcomCookieProvider
