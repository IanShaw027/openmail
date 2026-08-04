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

import json
import re
import time
import uuid
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

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
# List view often has subject only — pull bodies for more rows so UI is not empty
MAX_DETAIL_HYDRATE = 8
# messagelist pagination: hard caps so a flaky pager cannot hang the fetch
MAX_LIST_PAGES = 10
MAX_LIST_RAW_ROWS = 200

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


def is_mailcom_login_failed_url(url: str | None) -> bool:
    """United Internet SSO redirects bad password to www.mail.com/logout/?ls=wd|te.

    Observed live (2026-08): wrong password → 303 Location:
    ``https://www.mail.com/logout/?ls=wd`` (wrong data) / ``ls=te`` (technical error).
    Without this check, Path A falls through to parse-failed / "unstable login".
    """
    if not url:
        return False
    u = url.lower()
    if "logout" not in u:
        return False
    # ls=wd wrong credentials; ls=te technical/login error treated as credential fail
    if re.search(r"[?&]ls=(wd|te)\b", u):
        return True
    if "/logout" in u and "mail.com" in u:
        # bare logout after login POST is almost always failed auth
        return True
    return False


def normalize_mailcom_success_url(raw: str | None) -> str:
    """Replace JS-only placeholders in homepage successURL.

    Live homepage form ships:
      successURL=https://$(clientName)-$(dataCenter).mail.com/login
    Browser JS substitutes clientName/dataCenter; server-side fetch must expand them.
    Defaults match US mail.com lightmailer / navigator cluster (lxa).
    """
    s = (raw or "").strip()
    if not s:
        return "https://navigator-lxa.mail.com/login"
    s = s.replace("$(clientName)", "navigator").replace("$(dataCenter)", "lxa")
    s = s.replace("${clientName}", "navigator").replace("${dataCenter}", "lxa")
    # If still broken template, force known good host
    if "$(" in s or "${" in s:
        return "https://navigator-lxa.mail.com/login"
    return s


def extract_wicket_redirect(xml_text: str) -> str:
    match = re.search(r"<redirect><!\[CDATA\[(.*?)\]\]></redirect>", xml_text or "")
    if not match:
        # sometimes without CDATA
        match = re.search(r"<redirect>(.*?)</redirect>", xml_text or "", re.I | re.S)
    if not match:
        raise RuntimeError("mail.com lightmailer 未返回启动跳转。")
    return match.group(1).strip()


# Paths to try for compose UI (relative to lightmailer origin)
_COMPOSE_PATHS = (
    "/compose",
    "/mailcompose",
    "/messagecompose",
    "/write",
    "/folderlist?compose=true",
)


def _lightmailer_origin(meta: dict[str, Any] | None = None, site: str = DEFAULT_SITE) -> str:
    meta = meta or {}
    for key in ("folder_url", "light_url", "mailbox_url", "start_url"):
        u = meta.get(key)
        if not u:
            continue
        try:
            p = urlparse(str(u))
            if p.scheme and p.netloc:
                return f"{p.scheme}://{p.netloc}"
        except Exception:
            continue
    bare = (site or DEFAULT_SITE).lstrip(".").removeprefix("www.")
    return f"https://lightmailer.{bare}"


def _pick_compose_form(forms: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the HTML form that looks like a compose/send form."""
    best: dict[str, Any] | None = None
    best_score = -1
    for form in forms:
        inputs = form.get("inputs") or {}
        names = " ".join(str(n).lower() for n in inputs)
        action = str(form.get("action") or "").lower()
        fid = str(form.get("id") or form.get("name") or "").lower()
        score = 0
        if any(x in names for x in ("to", "recipient", "rcpt", "mail_to", "mailto")):
            score += 3
        if any(x in names for x in ("subject", "subj", "betreff")):
            score += 2
        if any(x in names for x in ("body", "content", "text", "message", "editor", "html")):
            score += 2
        if any(x in action for x in ("compose", "send", "write", "message")):
            score += 2
        if any(x in fid for x in ("compose", "send", "write", "mail")):
            score += 2
        # Prefer POST
        if str(form.get("method") or "get").lower() == "post":
            score += 1
        if score > best_score:
            best_score = score
            best = form
    return best if best_score >= 3 else None


def _fill_compose_payload(
    form: dict[str, Any],
    *,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
) -> dict[str, str]:
    """Map openmail fields onto whatever names the compose form uses."""
    payload: dict[str, str] = {}
    to_joined = ", ".join(to)
    body_value = (body_html or body_text or "").strip()
    plain = (body_text or "").strip()
    if not plain and body_html:
        plain = re.sub(r"<[^>]+>", " ", body_html)
        plain = re.sub(r"\s+", " ", plain).strip()

    to_set = subject_set = body_set = False
    for name, info in (form.get("inputs") or {}).items():
        itype = (info.get("type") or "text").lower()
        val = info.get("value") or ""
        lname = str(name).lower()
        if itype in ("submit", "button", "image", "file"):
            # Keep named submit buttons that look like Send
            if itype == "submit" and any(
                x in lname or x in val.lower()
                for x in ("send", "submit", "senden", "absenden")
            ):
                payload[name] = val or "Send"
            elif val and itype != "file":
                # hidden submit tokens
                if itype == "hidden":
                    payload[name] = val
            continue
        if itype == "hidden":
            payload[name] = val
            continue
        if any(
            x in lname
            for x in ("to", "recipient", "rcpt", "mail_to", "mailto", "an:")
        ) and "token" not in lname:
            payload[name] = to_joined
            to_set = True
        elif any(x in lname for x in ("cc", "bcc")):
            # leave empty unless prefilled
            payload[name] = val
        elif any(x in lname for x in ("subject", "subj", "betreff")):
            payload[name] = subject or ""
            subject_set = True
        elif any(
            x in lname
            for x in ("body", "content", "text", "message", "editor", "html", "mailtext")
        ):
            # Prefer HTML field when name hints html
            if "html" in lname and body_html:
                payload[name] = body_html
            else:
                payload[name] = body_value or plain
            body_set = True
        else:
            payload[name] = val

    # Fallbacks if form used non-standard names
    if not to_set:
        payload.setdefault("to", to_joined)
        payload.setdefault("recipients", to_joined)
    if not subject_set:
        payload.setdefault("subject", subject or "")
    if not body_set:
        payload.setdefault("body", body_value or plain)
        payload.setdefault("text", plain)
        if body_html:
            payload.setdefault("html", body_html)
    return payload


def _compose_looks_sent(html: str, status: int, final_url: str) -> bool:
    if status >= 400:
        return False
    h = (html or "").lower()
    u = (final_url or "").lower()
    if any(
        x in h
        for x in (
            "messagesent",
            "message sent",
            "mail sent",
            "has been sent",
            "successfully sent",
            "发送成功",
            "已发送",
            "nachricht gesendet",
            "email sent",
        )
    ):
        return True
    if any(x in u for x in ("sent", "success", "folderlist", "messagelist")):
        # landed back on mailbox after compose — weak success
        if status in (200, 302, 303) and "compose" not in u:
            if "error" not in h and "invalid" not in h:
                return True
    return False


# Modern mail.com webmailer (2026) — captured from browser compose/send:
# POST https://webmail-cats-live.mail.com/mailbox/primary/mailsubmission
# Authorization: Bearer qX{JWT}  (oauth2.mail.com / oauthbridge, scope mail_mailbox_w)
# Content-Type: application/vnd.ui.trinity.minimalmailmessage+json
#
# Token (captured):
#   POST oauthbridge…/navigator/oauth2/token?sid={sid}
#   Authorization: Basic base64(mailcom_mailcompose_passport_live:*******)
#   Content-Type: application/x-www-form-urlencoded
#   body: grant_type=urn:mam:oauth:grant-type:spa&scope={scope}
#   → {"access_token":"qX…","token_type":"Bearer","expires_in":3600,"scope":…}
CATS_BASE = "https://webmail-cats-live.mail.com"
WEBMAILER_ORIGIN = "https://webmailer.mail.com"
COMPOSE_X_UI_APP = "mailcom.webmailer.mail-compose/1.43.5"
COMPOSE_CLIENT_ID = "mailcom_mailcompose_passport_live"
# Public HTML / Basic auth use literal asterisks as the "secret"; real auth is cookies+sid.
COMPOSE_CLIENT_SECRET = "*******"
COMPOSE_GRANT_TYPE = "urn:mam:oauth:grant-type:spa"
COMPOSE_SCOPE_W = "mail_mailbox_w"
COMPOSE_SCOPE_R = "mail_mailbox_r"
OAUTH_BRIDGE_TOKEN = (
    "https://oauthbridge.navigator-lxa.mail.com/navigator/oauth2/token"
)
# Real browser sid is a long hex on ?sid=… (≈ 80–120 chars). The `navigator=`
# cookie is a shorter session hash — never treat it as sid.
_SID_QUERY_RE = re.compile(r"[?&]sid=([0-9a-fA-F]{40,160})")
_AUTH_ID_RE = re.compile(r"\b(a-[A-Za-z0-9_-]{10,})\b")


def _navigator_cluster(meta: dict[str, Any] | None = None) -> str:
    """Return navigator host like navigator-lxa.mail.com from session meta/cookies."""
    meta = meta or {}
    for key in ("folder_url", "light_url", "start_url", "navigator_url"):
        u = meta.get(key)
        if not u:
            continue
        host = (urlparse(str(u)).hostname or "").lower()
        if host.startswith("navigator-") and host.endswith(".mail.com"):
            return host
        if "lightmailer" in host:
            # lightmailer.mail.com ↔ navigator-lxa.mail.com (US default)
            return "navigator-lxa.mail.com"
    return "navigator-lxa.mail.com"


def _sid_from_text(text: str | None) -> str | None:
    """Pull navigator ?sid=… (long hex) from URL or HTML — not the navigator cookie."""
    if not text:
        return None
    m = _SID_QUERY_RE.search(text)
    if m:
        return m.group(1)
    m2 = re.search(r'["\']sid["\']\s*[:=]\s*["\']([0-9a-fA-F]{40,160})["\']', text)
    if m2:
        return m2.group(1)
    return None


def _extract_sid_from_client(client: Any, meta: dict[str, Any] | None = None) -> str | None:
    """sid appears on navigator URLs (?sid=…) and session_meta after login/restore.

    Do **not** use the ``navigator`` cookie value — capture shows that is a different
    short hash, while send/refresh use the long hex sid query param.
    """
    meta = meta or {}
    for key in ("sid", "session_id", "navigator_sid"):
        val = meta.get(key)
        if val and len(str(val)) >= 40:
            return str(val)
    for key in ("folder_url", "light_url", "start_url", "navigator_url"):
        sid = _sid_from_text(str(meta.get(key) or ""))
        if sid:
            return sid
    # Cookie jar: some stacks store sid in a dedicated cookie (not "navigator")
    jar = getattr(client, "cookies", None)
    if jar is not None:
        for name in ("sid", "SID", "ngsid", "session_sid"):
            try:
                val = jar.get(name)  # type: ignore[call-arg]
                if val and len(str(val)) >= 40:
                    return str(val)
            except Exception:
                pass
            try:
                for cookie in getattr(jar, "jar", []):
                    if str(getattr(cookie, "name", "")).lower() == name.lower():
                        val = str(getattr(cookie, "value", "") or "")
                        if len(val) >= 40:
                            return val
            except Exception:
                pass
    return None


def _harvest_session_markers(meta: dict[str, Any], text: str | None, url: str | None = None) -> None:
    """Update meta with sid / auth_id found in a response URL or body."""
    for candidate in (url, text):
        sid = _sid_from_text(candidate)
        if sid:
            meta["sid"] = sid
            if url and "navigator-" in (url or ""):
                meta["navigator_url"] = url
            break
    if text:
        m = _AUTH_ID_RE.search(text)
        if m:
            meta["auth_id"] = m.group(1)
        # webmailer bootstrap embeds authConfig + no_cache-like ids
        for m2 in re.finditer(
            r'auth_id["\']?\s*[:=]\s*["\']?(a-[A-Za-z0-9_-]{10,})', text
        ):
            meta["auth_id"] = m2.group(1)
            break


def _ensure_navigator_session(
    client: Any, *, meta: dict[str, Any], site: str = DEFAULT_SITE
) -> dict[str, Any]:
    """Hit navigator + webmailer so sid / passport cookies exist for oauthbridge.

    Captured flow keeps session warm with:
      POST /refresh?sid=…  body {"checkNewMails":false}
    """
    host = _navigator_cluster(meta)
    sid = _extract_sid_from_client(client, meta)
    referer = meta.get("folder_url") or meta.get("navigator_url") or MAIL_HOME_URL
    headers_html = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": str(referer),
        "User-Agent": USER_AGENT,
    }

    # 1) Warm webmailer shell (sets passport-related cookies / authConfig)
    for url in (WEBMAILER_ORIGIN + "/", f"https://{host}/mail" + (f"?sid={sid}" if sid else "")):
        try:
            resp = client.get(url, headers=headers_html)
            final = str(getattr(resp, "url", url))
            _harvest_session_markers(meta, _resp_text(resp), final)
            sid = meta.get("sid") or sid
        except Exception:
            continue

    # 2) Navigator refresh (browser heartbeat while composing)
    sid = _extract_sid_from_client(client, meta) or sid
    if sid:
        refresh_url = f"https://{host}/refresh?sid={sid}"
        try:
            resp = client.post(
                refresh_url,
                content=json.dumps({"checkNewMails": False}),
                headers={
                    "Accept": "*/*",
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": f"https://{host}",
                    "Referer": f"https://{host}/mail?sid={sid}",
                    "User-Agent": USER_AGENT,
                },
            )
            _harvest_session_markers(meta, _resp_text(resp), str(getattr(resp, "url", refresh_url)))
        except Exception:
            pass
        # also GET mail UI with sid
        try:
            mail_url = f"https://{host}/mail?sid={sid}"
            resp = client.get(mail_url, headers=headers_html)
            _harvest_session_markers(meta, _resp_text(resp), str(getattr(resp, "url", mail_url)))
            meta["navigator_url"] = f"https://{host}/mail?sid={sid}"
            meta["sid"] = sid
        except Exception:
            pass
    else:
        # No sid yet — probe navigator entry and follow redirects
        for url in (f"https://{host}/mail", f"https://{host}/"):
            try:
                resp = client.get(url, headers=headers_html)
                final = str(getattr(resp, "url", url))
                _harvest_session_markers(meta, _resp_text(resp), final)
                if meta.get("sid"):
                    break
            except Exception:
                continue
    return meta


def _coerce_token_string(value: Any) -> str | None:
    """Accept only string JWT-like tokens (never nested dicts)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_token_response(
    text: str, meta: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Extract (access_token, auth_id) from oauthbridge JSON or bare JWT."""
    data: Any = None
    try:
        data = json.loads(text) if text else None
    except Exception:
        data = None
    if isinstance(data, dict):
        # Nested shapes: {token:{access_token}}, {data:{…}}
        candidates: list[dict[str, Any]] = [data]
        for nest in ("token", "data", "result", "payload"):
            inner = data.get(nest)
            if isinstance(inner, dict):
                candidates.append(inner)
        for d in candidates:
            token = None
            nested_auth: Any = None
            for key in (
                "access_token",
                "accessToken",
                "id_token",
                "idToken",
                "token",
            ):
                raw = d.get(key)
                token = _coerce_token_string(raw)
                if token:
                    break
                if isinstance(raw, dict):
                    token = _coerce_token_string(
                        raw.get("access_token")
                        or raw.get("accessToken")
                        or raw.get("token")
                    )
                    if token:
                        nested_auth = (
                            raw.get("auth_id")
                            or raw.get("authId")
                            or raw.get("no_cache")
                        )
                        break
            auth_id = (
                nested_auth
                or d.get("auth_id")
                or d.get("authId")
                or d.get("no_cache")
                or meta.get("auth_id")
            )
            if token:
                if not auth_id:
                    auth_id = _auth_id_from_jwt(token)
                return token, str(auth_id) if auth_id else None
    # bare JWT (optionally qX-prefixed)
    if text:
        tok = text.strip().strip('"')
        if tok.startswith("qX") and tok.count(".") >= 2:
            return tok, _auth_id_from_jwt(tok) or meta.get("auth_id")
        if tok.count(".") >= 2 and len(tok) > 40:
            return tok, _auth_id_from_jwt(tok) or meta.get("auth_id")
    return None, None


def _compose_basic_auth() -> str:
    """Basic base64(client_id:*******) — secret is literal asterisks in the browser."""
    import base64

    raw = f"{COMPOSE_CLIENT_ID}:{COMPOSE_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _obtain_compose_token(
    client: Any, *, meta: dict[str, Any], scope: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """Request passport JWT for compose via oauthbridge SPA grant.

    Captured (2026-08):
      POST …/navigator/oauth2/token?sid={sid}
      Authorization: Basic base64(mailcom_mailcompose_passport_live:*******)
      Content-Type: application/x-www-form-urlencoded
      body: grant_type=urn:mam:oauth:grant-type:spa&scope={scope}
      → {"access_token":"qX…","token_type":"Bearer","expires_in":3600,"scope":…}

    Returns (access_token, auth_id, error). For send, scope defaults to mail_mailbox_w.
    """
    host = _navigator_cluster(meta)
    base_url = f"https://oauthbridge.{host}/navigator/oauth2/token"
    if host == "navigator-lxa.mail.com":
        base_url = OAUTH_BRIDGE_TOKEN

    sid = _extract_sid_from_client(client, meta)
    if not sid:
        return None, None, "缺少 navigator sid，请先取件刷新 mail.com 会话"

    scopes = [scope] if scope else [COMPOSE_SCOPE_W, COMPOSE_SCOPE_R]
    # de-dupe while preserving order
    seen: set[str] = set()
    scope_list: list[str] = []
    for s in scopes:
        if s and s not in seen:
            seen.add(s)
            scope_list.append(s)

    token_url = f"{base_url}?sid={sid}"
    headers = {
        "Accept": "*/*",
        "Authorization": _compose_basic_auth(),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": WEBMAILER_ORIGIN,
        "Referer": WEBMAILER_ORIGIN + "/",
        "User-Agent": USER_AGENT,
        "x-ui-app": COMPOSE_X_UI_APP,
    }
    last_err = "无法获取 mail.com 发信令牌 (oauthbridge)"
    for sc in scope_list:
        form = {
            "grant_type": COMPOSE_GRANT_TYPE,
            "scope": sc,
        }
        try:
            resp = client.post(token_url, data=form, headers=headers)
        except Exception as exc:
            last_err = f"oauthbridge 请求失败: {exc}"
            continue
        text = _resp_text(resp)
        status = _resp_status(resp)
        if status >= 400:
            snippet = (text or "").replace("\n", " ")[:120]
            last_err = f"oauthbridge HTTP {status}" + (
                f": {snippet}" if snippet else ""
            )
            continue
        token, auth_id = _parse_token_response(text or "", meta)
        if token:
            if auth_id:
                meta["auth_id"] = str(auth_id)
            # Prefer the requested scope when present in JWT
            return token, auth_id or _auth_id_from_jwt(token), None
        if text:
            last_err = f"oauthbridge 响应无令牌: {(text or '')[:120]}"
    return None, None, last_err


def _auth_id_from_jwt(token: str) -> str | None:
    """Decode JWT payload (no verify) for auth_id claim."""
    try:
        import base64

        raw = token[2:] if token.startswith("qX") else token
        parts = raw.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        aid = payload.get("auth_id") or payload.get("authId")
        return str(aid) if aid else None
    except Exception:
        return None


def _jwt_scope(token: str) -> str | None:
    try:
        import base64

        raw = token[2:] if token.startswith("qX") else token
        parts = raw.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        return str(payload.get("scope") or "") or None
    except Exception:
        return None


def _normalize_bearer(token: str) -> str:
    """Browser sends Authorization: Bearer qX{jwt}."""
    t = (token or "").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if not t.startswith("qX") and t.count(".") >= 2:
        t = "qX" + t
    return t


def _build_submission_body(
    *,
    from_addr: str,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    reply_to_id: str | None = None,
) -> dict[str, Any]:
    """Body shape from browser capture (minimalmailmessage).

    Live compose sends htmlBody with plaintextBody=null (not a dual plain/html pair).
    """
    html = (body_html or "").strip()
    plain = (body_text or "").strip()
    if not html and plain:
        esc = (
            plain.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        html = f"<html><body>{esc}</body></html>"
        # After wrapping plain → html, match browser: plaintextBody null
        plain_out: str | None = None
    elif html:
        # Prefer HTML-only like webmailer quick-reply / compose
        plain_out = None
    else:
        plain_out = plain or None
    header: dict[str, Any] = {
        "messageType": "MAIL",
        "from": from_addr,
        "to": list(to),
        "cc": [],
        "bcc": [],
        "subject": subject or "",
    }
    body: dict[str, Any] = {
        "mailHeader": header,
        "htmlBody": html or None,
        "plaintextBody": plain_out,
        "mailClientMeta": {"mail-drop": None},
        "transientMailProperties": {},
        "attachments": [],
    }
    if reply_to_id:
        body["transientMailProperties"] = {"reply": str(reply_to_id)}
    return body


def _cats_mailsubmission_send(
    client: Any,
    *,
    from_addr: str,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    meta: dict[str, Any] | None,
    site: str = DEFAULT_SITE,
) -> tuple[bool, str | None]:
    """Send via webmail-cats mailsubmission API (real browser path).

    Captured flow (2026-08 quick-reply Send):
      1. Session cookies + navigator refresh?sid=
      2. Passport JWT via oauthbridge (scope mail_mailbox_w, client mailcom_mailcompose_passport_live)
      3. POST /mailbox/primary/mailsubmission?absoluteURI=false&no_cache={auth_id}
         Authorization: Bearer qX{jwt}
         Content-Type: application/vnd.ui.trinity.minimalmailmessage+json
         x-ui-app: mailcom.webmailer.mail-compose/1.43.5
    """
    meta = dict(meta or {})
    meta = _ensure_navigator_session(client, meta=meta, site=site)
    # Send needs write scope; SPA grant returns a token whose JWT.scope matches the request
    token, auth_id, err = _obtain_compose_token(
        client, meta=meta, scope=COMPOSE_SCOPE_W
    )
    if not token:
        # Fallback: legacy lightmailer HTML compose (often broken on modern UI)
        return _legacy_html_compose_send(
            client,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            meta=meta,
            site=site,
            prefix_err=err,
        )

    # Prefer write-scope token; reject pure read tokens early with a clear error
    scope = _jwt_scope(token) or ""
    if scope and COMPOSE_SCOPE_W not in scope:
        if scope == COMPOSE_SCOPE_R or scope == "mail_mailbox_r":
            return False, (
                "mail.com 发信令牌仅有读权限 (mail_mailbox_r)，"
                "请重新取件刷新会话后再试"
            )

    auth_id = auth_id or meta.get("auth_id") or _auth_id_from_jwt(token)
    if not auth_id:
        return False, "mail.com 发信缺少 auth_id（no_cache），请重新取件后再试"
    meta["auth_id"] = auth_id
    bearer = _normalize_bearer(token)
    url = (
        f"{CATS_BASE}/mailbox/primary/mailsubmission"
        f"?absoluteURI=false&no_cache={auth_id}"
    )
    payload = _build_submission_body(
        from_addr=from_addr,
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    req_id = str(uuid.uuid4())
    try:
        resp = client.post(
            url,
            content=json.dumps(payload, ensure_ascii=False),
            headers={
                "Accept": "text/plain",
                "Authorization": f"Bearer {bearer}",
                "Content-Type": (
                    "application/vnd.ui.trinity.minimalmailmessage+json; charset=utf-8"
                ),
                "Origin": WEBMAILER_ORIGIN,
                "Referer": WEBMAILER_ORIGIN + "/",
                "User-Agent": USER_AGENT,
                "x-ui-app": COMPOSE_X_UI_APP,
                "x-request-id": req_id,
            },
        )
    except Exception as exc:
        return False, f"mailsubmission 请求失败: {exc}"

    status = _resp_status(resp)
    text = _resp_text(resp)
    if status in (200, 201, 202, 204):
        return True, None
    if status in (401, 403):
        return False, f"mail.com 发信鉴权失败 ({status})，请重新取件刷新会话后再试"
    # Sometimes returns empty 200-equivalent body with other codes
    if status < 400 and not text:
        return True, None
    snippet = (text or "").replace("\n", " ")[:180]
    return False, f"mail.com 发信失败 ({status}){(': ' + snippet) if snippet else ''}"


def _legacy_html_compose_send(
    client: Any,
    *,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    meta: dict[str, Any] | None,
    site: str = DEFAULT_SITE,
    prefix_err: str | None = None,
) -> tuple[bool, str | None]:
    """Best-effort lightmailer HTML form send (fallback if passport token fails)."""
    origin = _lightmailer_origin(meta, site)
    compose_html = ""
    compose_url = ""
    for path in _COMPOSE_PATHS:
        url = urljoin(origin + "/", path.lstrip("/"))
        try:
            resp = client.get(
                url,
                headers={
                    "Referer": (meta or {}).get("folder_url") or origin + "/folderlist",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        except Exception:
            continue
        html = _resp_text(resp)
        if _resp_status(resp) >= 400 or _looks_like_session_loss(html):
            continue
        if _pick_compose_form(parse_forms(html)):
            compose_html = html
            compose_url = str(getattr(resp, "url", url))
            break
    form = _pick_compose_form(parse_forms(compose_html)) if compose_html else None
    if not form:
        base = prefix_err or "无法获取发信令牌"
        return (
            False,
            f"{base}；且 lightmailer 写邮件页不可用。"
            "请先成功取件刷新 Cookie 后重试。",
        )
    action = form.get("action") or compose_url
    post_url = urljoin(compose_url or origin + "/", action)
    payload = _fill_compose_payload(
        form,
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    try:
        post = client.post(
            post_url,
            data=payload,
            headers={
                "Referer": compose_url or origin,
                "Origin": origin,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except Exception as exc:
        return False, f"mail.com 发信提交失败: {exc}"
    if _compose_looks_sent(
        _resp_text(post), _resp_status(post), str(getattr(post, "url", post_url))
    ):
        return True, None
    return False, "mail.com 发信未确认成功（passport API 与表单回退均失败）"


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


def html_indicates_bad_credentials(html: str | None, url: str | None = None) -> bool:
    """True only when the page clearly reports bad password/login — not marketing copy."""
    if is_mailcom_login_failed_url(url):
        return True
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
    """Errors that often succeed on retry (parse/network/ott/rate-limit).

    Clear wrong-password is NOT transient — do not rewrite to "login unstable".
    """
    if not err:
        return True
    if err == "账号或密码错误" or "密码错误" in err:
        return False
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


def _page_index_from_url(url: str) -> int | None:
    """Best-effort page/offset number from a list URL (None if absent)."""
    if not url:
        return None
    m = re.search(r"[?&](?:page|offset|start|first|from)=(\d+)", url, re.I)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def extract_messagelist_next_url(listing_url: str, listing_html: str) -> str | None:
    """Find the next messagelist page URL from lightmailer / generic list HTML.

    Supports common patterns: rel=next, aria-label Next, class next, page=N links
    that point at messagelist. Skips prev/lower page indices. Returns absolute URL or None.
    """
    html = listing_html or ""
    if not html:
        return None
    import html as html_mod

    # (priority, rel_href) — lower priority number wins
    scored: list[tuple[int, str]] = []

    def _add(priority: int, rel: str) -> None:
        if rel:
            scored.append((priority, html_mod.unescape(rel)))

    # rel="next" — highest confidence
    for m in re.finditer(
        r'<a[^>]+rel=["\']next["\'][^>]*href=["\']([^"\']+)["\']',
        html,
        re.I,
    ):
        _add(0, m.group(1))
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']next["\']',
        html,
        re.I,
    ):
        _add(0, m.group(1))

    # aria-label / title containing next (not prev)
    for m in re.finditer(
        r'<a[^>]+(?:aria-label|title)=["\']([^"\']*)["\'][^>]*href=["\']([^"\']+)["\']',
        html,
        re.I,
    ):
        label, href = m.group(1), m.group(2)
        low = label.lower()
        if re.search(r"prev|previous|上一页|«|‹", low):
            continue
        if re.search(r"next|下一页|»|›", low):
            _add(1, href)
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*(?:aria-label|title)=["\']([^"\']*)["\']',
        html,
        re.I,
    ):
        href, label = m.group(1), m.group(2)
        low = label.lower()
        if re.search(r"prev|previous|上一页|«|‹", low):
            continue
        if re.search(r"next|下一页|»|›", low):
            _add(1, href)

    # class*="next" / pagination__next (not prev)
    for m in re.finditer(
        r'<a[^>]+class=["\']([^"\']*)["\'][^>]*href=["\']([^"\']+)["\']',
        html,
        re.I,
    ):
        cls, href = m.group(1), m.group(2)
        cl = cls.lower()
        if re.search(r"prev|previous", cl):
            continue
        if re.search(r"pagination__next|pager-next|nav-next|\bnext\b", cl):
            _add(1, href)
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\']([^"\']*)["\']',
        html,
        re.I,
    ):
        href, cls = m.group(1), m.group(2)
        cl = cls.lower()
        if re.search(r"prev|previous", cl):
            continue
        if re.search(r"pagination__next|pager-next|nav-next|\bnext\b", cl):
            _add(1, href)

    # Link text Next / 下一页
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*(?:Next|下一页|»|›)\s*</a>',
        html,
        re.I,
    ):
        _add(1, m.group(1))

    # Explicit Prev text — never
    # Messagelist page/offset links: only if index > current (lower confidence)
    cur_page = _page_index_from_url(listing_url) or 1
    for m in re.finditer(
        r'href=["\']([^"\']*messagelist[^"\']*(?:page|offset|start|first|from)=[^"\']+)["\']',
        html,
        re.I,
    ):
        rel = m.group(1)
        # skip if surrounding context looks like prev (cheap check on full match window later)
        abs_try = urljoin(listing_url or "", html_mod.unescape(rel).replace("&amp;", "&"))
        nxt_page = _page_index_from_url(abs_try)
        if nxt_page is not None and nxt_page > cur_page:
            _add(2, rel)

    base = listing_url or ""
    base_key = base.split("#", 1)[0].rstrip("/")
    seen: set[str] = set()
    scored.sort(key=lambda x: x[0])
    for _prio, rel in scored:
        if not rel or rel.startswith("#") or rel.lower().startswith("javascript:"):
            continue
        # skip explicit prev link text patterns already partially filtered
        abs_url = urljoin(base, rel.replace("&amp;", "&"))
        key = abs_url.split("#", 1)[0].rstrip("/")
        if key in seen or key == base_key:
            continue
        seen.add(key)
        # Reject lower/equal page index when both sides have numbers
        nxt_page = _page_index_from_url(abs_url)
        if nxt_page is not None and nxt_page <= cur_page:
            continue
        low = abs_url.lower()
        if "messagelist" in low or "page=" in low or "offset=" in low or "start=" in low:
            return abs_url
        if _prio <= 1:
            return abs_url
    return None


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


def collect_messagelist_with_paging(
    client: Any,
    *,
    first_url: str,
    first_html: str,
    limit: int,
    folder: str,
    max_pages: int = MAX_LIST_PAGES,
    max_raw: int = MAX_LIST_RAW_ROWS,
) -> list[Message]:
    """Parse first list page then follow next links until limit / caps.

    Uses the same HTTP client (session cookies) — no re-login between pages.
    """
    by_id: dict[str, Message] = {}
    order: list[str] = []
    page_url = first_url
    page_html = first_html
    pages_seen = 0
    visited: set[str] = set()

    while page_url and pages_seen < max_pages and len(order) < max(limit, 1):
        key = page_url.split("#", 1)[0].rstrip("/")
        if key in visited:
            break
        visited.add(key)
        pages_seen += 1

        if pages_seen == 1:
            html = page_html
            url = page_url
        else:
            try:
                resp = client.get(page_url)
            except Exception:
                break
            html = _resp_text(resp)
            url = str(getattr(resp, "url", page_url))
            if not html or _looks_like_session_loss(html):
                break

        # Per-page: parse without artificial tiny limit so next-page still useful
        page_limit = max(limit, min(max_raw, limit * 3 if limit else max_raw))
        if "message-list__item" in (html or ""):
            batch = parse_lightmailer_message_list(url, html, limit=page_limit, folder=folder)
        else:
            batch = parse_message_list_html(html, limit=page_limit, folder=folder)

        if not batch:
            break

        for msg in batch:
            mid = str(msg.id or "").strip()
            if not mid or mid in by_id:
                continue
            by_id[mid] = msg
            order.append(mid)
            if len(order) >= max_raw:
                break

        if len(order) >= limit or len(order) >= max_raw:
            break

        nxt = extract_messagelist_next_url(url, html)
        if not nxt or nxt.split("#", 1)[0].rstrip("/") in visited:
            break
        page_url = nxt

    out = [by_id[i] for i in order[:limit]]
    return out


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


# Marketing / chrome titles that must never become subject or preview.
_MAILCOM_CHROME_TITLE_RE = re.compile(
    r"(secure\s*&\s*free\s*webmail|webmail\s+features\s+for\s+your\s+mail|"
    r"free\s+email\s+accounts\s+with\s+mail\.com|"
    r"log\s+in\s+here\s+or\s+register|"
    r"^\s*mail\.com\s*$|"
    r"message\s*-\s*mail\.com)",
    re.I,
)


def _strip_tags(html_frag: str) -> str:
    import html as html_mod

    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_frag or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_chrome_title(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return bool(_MAILCOM_CHROME_TITLE_RE.search(t))


def extract_mailbody_iframe_src(html: str) -> str | None:
    """lightmailer puts the real message HTML in ./mailbody/{mailId}/false iframe."""
    if not html:
        return None
    # Prefer mailbody iframe
    for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1).strip()
        if not src or src.startswith("about:") or "generic_dfp" in src or "keepalive" in src:
            continue
        if "mailbody" in src.lower():
            return src
    return None


def parse_message_detail_html(html: str, *, msg_id: str, folder: str = "inbox") -> Message:
    """Extract subject/from/body from a detail page, fixture, or mailbody iframe HTML.

    Live lightmailer (2026-08): shell page uses ``mail-header__*`` + iframe
    ``./mailbody/{id}/false`` for the real body. Fixture still uses ``.mail-body``.
    """
    subject = ""
    from_ = ""
    body_text = ""
    body_html = ""
    date_s = ""

    # ── subject ──────────────────────────────────────────────────────
    for pat in (
        r'<h1[^>]*class=["\'][^"\']*subject[^"\']*["\'][^>]*>(.*?)</h1>',
        r'class=["\'][^"\']*mail-header__subject[^"\']*["\'][^>]*>(.*?)</(?:dd|div|span|h1|h2|td)>',
        r'class=["\'][^"\']*mail-header__subject[^"\']*["\'][^>]*>(.*?)</',
    ):
        sm = re.search(pat, html or "", re.I | re.S)
        if sm:
            subject = _strip_tags(sm.group(1))
            if subject and not _is_chrome_title(subject):
                break
            subject = ""

    if not subject:
        sm = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
        if sm:
            cand = _strip_tags(sm.group(1))
            if cand and not _is_chrome_title(cand):
                subject = cand

    # ── from ─────────────────────────────────────────────────────────
    for pat in (
        r'class=["\'][^"\']*mail-header__sender[^"\']*["\'][^>]*title=["\']([^"\']+)["\']',
        r'class=["\'][^"\']*mail-header__sender[^"\']*["\'][^>]*>(.*?)</(?:dd|div|span|td)>',
        r'<[^>]+class=["\'][^"\']*\bfrom\b[^"\']*["\'][^>]*>(.*?)</',
    ):
        fm = re.search(pat, html or "", re.I | re.S)
        if fm:
            from_ = _strip_tags(fm.group(1))
            # skip pure labels like "From:"
            if from_ and from_.lower() not in ("from", "from:", "sender"):
                break
            from_ = ""

    # ── date (optional) ──────────────────────────────────────────────
    dm = re.search(
        r'class=["\'][^"\']*mail-header__date[^"\']*["\'][^>]*>(.*?)</',
        html or "",
        re.I | re.S,
    )
    if dm:
        date_s = _strip_tags(dm.group(1))

    # ── body: inline containers (fixtures + some themes) ─────────────
    # Do NOT use message-detail-panel__body — live lightmailer only embeds an
    # <iframe src="./mailbody/..."> there (no real content).
    bm = re.search(
        r'<div[^>]+class=["\'][^"\']*mail-body[^"\']*["\'][^>]*>(.*?)</div>',
        html or "",
        re.I | re.S,
    )
    if bm:
        frag = bm.group(1).strip()
        # Ignore shell that is only an iframe to mailbody
        if "mailbody" in frag.lower() and len(_strip_tags(frag)) < 40:
            frag = ""
        if frag:
            body_html = frag
            body_text = _strip_tags(body_html)
    if not body_text:
        pm = re.search(
            r'<pre[^>]+class=["\'][^"\']*body-text[^"\']*["\'][^>]*>(.*?)</pre>',
            html or "",
            re.I | re.S,
        )
        if pm:
            body_text = _strip_tags(pm.group(1))

    # If this is already a mailbody iframe document (almost pure email HTML)
    low = (html or "").lower()
    is_shell = "message-detail-panel" in low or "mail-header__subject" in low
    if not body_text and not body_html and html and not is_shell:
        if "folderlist" not in low and (
            "mime" in low
            or "<table" in low
            or "verification" in low
            or len(html) > 400
            or "<html" in low
        ):
            body_html = html
            body_text = _strip_tags(html)

    # Never use marketing chrome as preview
    preview_src = body_text or subject
    if _is_chrome_title(preview_src):
        preview_src = body_text or ""

    addr_m = re.search(r"[\w.+-]+@[\w.-]+", from_)
    msg = Message(
        id=msg_id,
        subject=subject,
        from_=from_,
        from_address=(addr_m.group(0).lower() if addr_m else ""),
        date=date_s or None,
        body_text=body_text,
        body_html=body_html,
        body_preview=(preview_src)[:280],
        folder=folder,
        verification_code=extract_verification_code(
            subject=subject, body_text=body_text, body_html=body_html
        ),
        raw_refs={"mailbody_iframe": extract_mailbody_iframe_src(html or "")},
    )
    return msg


class MailcomCookieProvider:
    """Cookie-class provider specialized for mail.com / lightmailer sites."""

    name = "cookie"
    # Local date filter applied after list fetch (no server-side cursor)
    time_paging = "local_filter"

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
        # When local since/before filter will run, pull enough raw rows to
        # keep paging past the first screen before the final time filter.
        list_limit = limit
        if limits and (
            limits.get("since")
            or limits.get("before")
            or limits.get("received_after")
            or limits.get("received_before")
        ):
            list_limit = max(limit, MAX_LIST_RAW_ROWS)

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
                    # Only soften when we saw real transient failures *and* never a
                    # clear wrong-password. Previously "账号或密码错误" was treated as
                    # transient and rewritten to "登录不稳定", hiding the real issue.
                    if final_err == "账号或密码错误":
                        pass  # keep clear credential error
                    elif final_err in (
                        "mail.com login parse failed",
                        "mail.com 登录失败",
                    ) or (
                        final_err
                        and any(
                            x in final_err
                            for x in ("ott", "parse", "未返回", "页面")
                        )
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
                client, folder=folder, limit=list_limit, site=site, meta=meta
            )
            # List rows are often subject-only (lightmailer). Pull detail HTML for bodies.
            hydrate_n = MAX_DETAIL_HYDRATE if quick else min(15, limit)
            for i, msg in enumerate(list(messages)):
                if i >= hydrate_n:
                    break
                if msg.body_text or msg.body_html:
                    continue
                try:
                    refs = getattr(msg, "raw_refs", None) or {}
                    detail_url = None
                    if isinstance(refs, dict):
                        detail_url = refs.get("detail_url") or refs.get("url")
                    detail = self.fetch_detail(
                        client,
                        msg.id,
                        folder=folder,
                        site=site,
                        meta=meta,
                        detail_url=str(detail_url) if detail_url else None,
                    )
                    if detail:
                        msg.subject = msg.subject or detail.subject
                        msg.from_ = msg.from_ or detail.from_
                        msg.from_address = msg.from_address or detail.from_address
                        if detail.body_text or detail.body_html:
                            msg.body_text = detail.body_text
                            msg.body_html = detail.body_html
                        # Never clobber list subject with chrome marketing titles
                        prev = (detail.body_preview or detail.body_text or "").strip()
                        if prev and not _is_chrome_title(prev):
                            msg.body_preview = prev[:280]
                        elif msg.body_text and not _is_chrome_title(msg.body_text):
                            msg.body_preview = msg.body_text[:280]
                        elif msg.subject and not _is_chrome_title(msg.subject):
                            msg.body_preview = msg.subject[:280]
                        msg.verification_code = detail.verification_code or msg.verification_code
                except Exception:
                    continue

            attach_verification_code(messages)
            # Local time filter: lightmailer has no since/before API
            if limits:
                from app.providers.base import filter_messages_by_time

                since_s = limits.get("since") or limits.get("received_after")
                before_s = limits.get("before") or limits.get("received_before")
                messages = filter_messages_by_time(
                    messages,
                    since=str(since_s) if since_s else None,
                    before=str(before_s) if before_s else None,
                )
                messages = messages[:limit]
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
            # Homepage ships successURL with $(clientName)-$(dataCenter) — expand for non-JS
            if "successURL" in fields or "successurl" in {k.lower() for k in fields}:
                for sk in list(fields.keys()):
                    if sk.lower() == "successurl":
                        fields[sk] = normalize_mailcom_success_url(fields.get(sk))
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
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            login_url = str(getattr(login, "url", post_url)).split("#", 1)[0]
            login_html = _resp_text(login)

            # Wrong password: SSO always lands on logout/?ls=wd (do not call this "parse failed")
            if is_mailcom_login_failed_url(login_url) or html_indicates_bad_credentials(
                login_html, login_url
            ):
                return False, "账号或密码错误", None

            if "FolderListPage" in login_html or session_looks_valid(login_html):
                return True, None, {"folder_url": login_url, "last_probe": "login_ok"}

            try:
                ott = extract_ott(login_url, login_html)
                # Prefer ott start URL from redirect host when available
                light_start = LIGHT_START_URL.format(ott=ott)
                if "mail.com" in login_url and "logout" not in login_url.lower():
                    # navigator-lxa.mail.com/login?ott=… already session-bearing
                    try:
                        nav = client.get(login_url)
                        nav_html = _resp_text(nav)
                        nav_url = str(getattr(nav, "url", login_url))
                        if "FolderListPage" in nav_html or session_looks_valid(nav_html):
                            return True, None, {
                                "folder_url": nav_url,
                                "last_probe": "login_nav_ok",
                            }
                        if "ott=" in nav_url.lower():
                            ott = extract_ott(nav_url, nav_html)
                            light_start = LIGHT_START_URL.format(ott=ott)
                    except Exception:
                        pass
                light = client.get(light_start)
                light_url = str(getattr(light, "url", light_start))
                light_html = _resp_text(light)
                if is_mailcom_login_failed_url(light_url):
                    return False, "账号或密码错误", None
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
                # no ott — try restore from cookies set by login POST, else Path B
                ok, meta = self.try_restore(
                    client, dump_client_cookies(client), site=site, meta=None
                )
                if ok:
                    return True, None, meta
                if is_mailcom_login_failed_url(login_url) or html_indicates_bad_credentials(
                    login_html, login_url
                ):
                    return False, "账号或密码错误", None
                if html_indicates_rate_limit(login_html):
                    return False, "mail.com 访问过于频繁或需要验证码，请稍后重试", None
                # Fall through to Path B (fixtures / alternate portals)
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
            post_url_final = str(getattr(post_resp, "url", post_url))
            if html_indicates_bad_credentials(post_html, post_url_final):
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

    def send_mail(
        self,
        *,
        to: list[str],
        subject: str,
        body_text: str = "",
        body_html: str | None = None,
        credentials: dict[str, Any] | None = None,
        account: Any = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Send via the same cookie/lightmailer session used for fetch.

        Returns (ok, error_message, session_writeback) where session_writeback is
        optional {cookies, session_meta} after a successful restore/login.
        """
        creds = dict(credentials or {})
        email_addr = (
            getattr(account, "email", None) or creds.get("email") or ""
        ).strip()
        password = creds.get("password") or getattr(account, "password", None) or ""
        site = str(creds.get("site") or DEFAULT_SITE)
        cookies = cookies_to_jar_list(
            creds.get("cookies") or creds.get("session_cookies") or creds.get("session")
        )
        meta = dict(creds.get("session_meta") or {})
        proxy = creds.get("proxy") or getattr(account, "proxy", None)
        recipients = [a.strip() for a in (to or []) if a and str(a).strip()]
        if not email_addr:
            return False, "缺少发件邮箱", None
        if not recipients:
            return False, "收件人不能为空", None

        client = None
        try:
            client = _http_client(
                self.timeout, proxy=proxy if isinstance(proxy, str) else None
            )
            session_ok = False
            if cookies:
                ok, meta_update = self.try_restore(
                    client, cookies, site=site, meta=meta
                )
                if ok:
                    session_ok = True
                    if meta_update:
                        meta.update(meta_update)
            if not session_ok:
                if not password:
                    return False, "会话失效，请补充密码后重试", None
                ok, err, meta_update = self.full_login(
                    client, email_addr, str(password), site=site
                )
                if not ok:
                    return False, err or "mail.com 登录失败", None
                if meta_update:
                    meta.update(meta_update)

            ok_send, err_send = _cats_mailsubmission_send(
                client,
                from_addr=email_addr,
                to=recipients,
                subject=subject or "",
                body_text=body_text or "",
                body_html=body_html,
                meta=meta,
                site=site,
            )
            writeback = {
                "cookies": dump_client_cookies(client),
                "session_meta": meta or None,
            }
            if not ok_send:
                return False, err_send or "mail.com 发信失败", writeback
            return True, None, writeback
        except Exception as exc:  # noqa: BLE001
            return False, f"mail.com 发信异常: {exc}", None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

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
                        msgs = collect_messagelist_with_paging(
                            client,
                            first_url=listing_url,
                            first_html=listing_html,
                            limit=limit,
                            folder=folder_l,
                        )
                        if msgs:
                            return msgs
                    except Exception:
                        pass

            if "message-list__item" in html:
                msgs = collect_messagelist_with_paging(
                    client,
                    first_url=final_url,
                    first_html=html,
                    limit=limit,
                    folder=folder_l,
                )
                if msgs:
                    return msgs
            msgs = collect_messagelist_with_paging(
                client,
                first_url=final_url,
                first_html=html,
                limit=limit,
                folder=folder_l,
            )
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
        detail_url: str | None = None,
    ) -> Message | None:
        """Fetch a single message body by lightmailer detail URL or id patterns.

        Live lightmailer: detail shell + iframe ``./mailbody/{mailId}/false`` holds
        the real HTML body — both must be fetched.
        """
        if not message_id and not detail_url:
            return None
        candidates: list[str] = []
        # Prefer URL captured from list page (messagedetail?mailId=…)
        if detail_url:
            candidates.append(str(detail_url))
        # message_id may itself be a full URL from parse_lightmailer_message_list
        mid = str(message_id or "")
        if mid.startswith("http://") or mid.startswith("https://") or mid.startswith("./"):
            candidates.append(mid)
        bases = _folder_urls(site, meta)
        folder_url = (meta or {}).get("folder_url") if meta else None
        if folder_url:
            bases = [str(folder_url), *bases]
        light_base = "https://lightmailer.mail.com"
        try:
            bare = urlparse(f"https://{site}").hostname or DEFAULT_SITE
            light_base = f"https://lightmailer.{bare}"
        except Exception:
            pass
        for base in bases:
            b = str(base).rstrip("/")
            # lightmailer relative detail
            if mid and mid.isdigit():
                candidates.extend(
                    [
                        f"{b}/messagedetail?mailId={mid}",
                        f"{b}/./messagedetail?mailId={mid}",
                        f"{light_base}/messagedetail?mailId={mid}",
                        # direct body iframe (works when session valid)
                        f"{light_base}/mailbody/{mid}/false",
                        f"{b}/mailbody/{mid}/false",
                    ]
                )
            if mid:
                candidates.extend(
                    [
                        f"{b}/mail/show/{mid}",
                        f"{b}/message/{mid}",
                        f"{b}/?msg={mid}",
                        f"{b}/mail?id={mid}",
                    ]
                )
        seen: set[str] = set()
        best: Message | None = None
        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                # resolve relative ./messagedetail against folder base
                if url.startswith("./") and folder_url:
                    url = urljoin(str(folder_url), url)
                resp = client.get(url)
            except Exception:
                continue
            html = _resp_text(resp)
            if not html or _resp_status(resp) >= 400:
                continue
            if _looks_like_session_loss(html) and not session_looks_valid(html):
                # pure mailbody docs won't have folder markers — allow if path is mailbody
                if "mailbody" not in url.lower():
                    continue
            final_url = str(getattr(resp, "url", url) or url)
            msg = parse_message_detail_html(
                html, msg_id=mid or str(message_id or "detail"), folder=folder.lower()
            )
            # Always follow mailbody iframe when present (shell has no real body)
            iframe_src = extract_mailbody_iframe_src(html)
            need_iframe = bool(iframe_src) and (
                not (msg.body_text or "").strip()
                or "mailbody" in (msg.body_html or "").lower()
                or len((msg.body_text or "").strip()) < 20
            )
            if need_iframe and iframe_src:
                try:
                    body_url = urljoin(final_url, iframe_src)
                    if body_url not in seen:
                        seen.add(body_url)
                        bresp = client.get(body_url)
                        bhtml = _resp_text(bresp)
                        if bhtml and _resp_status(bresp) < 400:
                            body_msg = parse_message_detail_html(
                                bhtml,
                                msg_id=mid or str(message_id or "detail"),
                                folder=folder.lower(),
                            )
                            if body_msg.body_html or body_msg.body_text:
                                msg.body_html = body_msg.body_html or msg.body_html
                                msg.body_text = body_msg.body_text or msg.body_text
                                if msg.body_text and not _is_chrome_title(msg.body_text):
                                    msg.body_preview = msg.body_text[:280]
                                if body_msg.verification_code and not msg.verification_code:
                                    msg.verification_code = body_msg.verification_code
                except Exception:
                    pass

            # Also try direct mailbody if still empty and we have numeric id
            if not (msg.body_text or msg.body_html) and mid.isdigit():
                for burl in (
                    f"{light_base}/mailbody/{mid}/false",
                    urljoin(final_url, f"./mailbody/{mid}/false"),
                ):
                    if burl in seen:
                        continue
                    seen.add(burl)
                    try:
                        bresp = client.get(burl)
                        bhtml = _resp_text(bresp)
                        if not bhtml or _resp_status(bresp) >= 400:
                            continue
                        body_msg = parse_message_detail_html(
                            bhtml,
                            msg_id=mid,
                            folder=folder.lower(),
                        )
                        if body_msg.body_html or body_msg.body_text:
                            msg.body_html = body_msg.body_html
                            msg.body_text = body_msg.body_text
                            if msg.body_text and not _is_chrome_title(msg.body_text):
                                msg.body_preview = msg.body_text[:280]
                            if body_msg.verification_code:
                                msg.verification_code = body_msg.verification_code
                            break
                    except Exception:
                        continue

            # Recompute preview; never leave marketing chrome
            if msg.body_text and not _is_chrome_title(msg.body_text):
                msg.body_preview = msg.body_text[:280]
            elif msg.subject and not _is_chrome_title(msg.subject):
                msg.body_preview = msg.subject[:280]
            else:
                msg.body_preview = (msg.body_preview or "")[:280]
                if _is_chrome_title(msg.body_preview):
                    msg.body_preview = ""

            if msg.subject or msg.body_text or msg.body_html:
                # Prefer a message that actually has body content
                if msg.body_text or msg.body_html:
                    return msg
                if best is None:
                    best = msg
        return best


# Back-compat alias used by registry
CookieMailcomProvider = MailcomCookieProvider
