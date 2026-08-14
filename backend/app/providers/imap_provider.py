"""IMAP provider: connect, list recent UIDs, parse MIME → Message.

Uses stdlib imaplib with timeouts. Junk folder tries Junk/Spam/Junk Email.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
import socket
import base64
from datetime import datetime, timezone
from email import policy as email_policy
from email.message import EmailMessage, Message as CompatMessage
from typing import Any

from app.providers.base import FetchResult, HealthResult, Message
from app.providers.imap_hosts import resolve_imap_host
from app.services.parser import annotate_message_code, attach_verification_code

# Type alias: both EmailMessage and legacy Message support walk/get_content_type
EmailPart = EmailMessage | CompatMessage

# Defaults
DEFAULT_TIMEOUT = 30.0
QUICK_LIMIT = 15
FULL_LIMIT = 50

INBOX_ALIASES = ("INBOX", "Inbox", "inbox")
JUNK_CANDIDATES = ("Junk", "Spam", "Junk Email", "Junk E-mail", "Bulk Mail", "垃圾邮件")
SENT_CANDIDATES = (
    "Sent",
    "Sent Items",
    "Sent Messages",
    "Sent Mail",
    "[Gmail]/Sent Mail",
    "已发送",
    "已发邮件",
)

# NetEase Coremail (126/163/yeah) rejects SELECT with "Unsafe Login" unless the
# client sends IMAP ID (RFC 2971) after LOGIN. CAPABILITY advertises "ID".
_NETEASE_IMAP_HOST_MARKERS = (
    "imap.126.com",
    "imap.163.com",
    "imap.yeah.net",
    "126.com",
    "163.com",
    "yeah.net",
)


def _imap_utf7_encode(name: str) -> str:
    """RFC 3501 modified UTF-7 for mailbox names."""
    if not name:
        return name
    out: list[str] = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            out.append("&-" if ch == "&" else ch)
            i += 1
            continue
        j = i
        while j < n and not (0x20 <= ord(name[j]) <= 0x7E):
            j += 1
        raw = name[i:j].encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        i = j
    return "".join(out)


def _imap_utf7_decode(name: str) -> str:
    """Decode RFC 3501 modified UTF-7 mailbox name."""
    if not name or "&" not in name:
        return name
    out: list[str] = []
    i = 0
    n = len(name)
    while i < n:
        amp = name.find("&", i)
        if amp < 0:
            out.append(name[i:])
            break
        out.append(name[i:amp])
        if amp + 1 < n and name[amp + 1] == "-":
            out.append("&")
            i = amp + 2
            continue
        end = name.find("-", amp + 1)
        if end < 0:
            out.append(name[amp:])
            break
        b64 = name[amp + 1 : end].replace(",", "/")
        pad = (-len(b64)) % 4
        try:
            raw = base64.b64decode(b64 + ("=" * pad))
            out.append(raw.decode("utf-16-be"))
        except Exception:
            out.append(name[amp : end + 1])
        i = end + 1
    return "".join(out)


def _mailbox_select_variants(name: str) -> list[str]:
    """Names to try with IMAP SELECT (raw + modified UTF-7)."""
    out: list[str] = []
    for n in (name, _imap_utf7_encode(name)):
        if n and n not in out:
            out.append(n)
    return out


# Control characters terminate an IMAP command line, and `%`/`*` are wildcards.
_MAILBOX_UNSAFE_RE = re.compile(r'[\x00-\x1f\x7f%*]')


def _quote_mailbox(name: str) -> str:
    """Return an RFC 3501 quoted mailbox name, or raise on an unusable one.

    imaplib does not quote or escape mailbox names: `_command` joins arguments
    with spaces and writes `data + CRLF` straight to the socket. A folder name
    containing CRLF therefore emits a second IMAP command in the same write —
    verified against a real IMAP dialogue, where
    `INBOX\\r\\nZ999 STORE 1:* +FLAGS (\\Deleted)` arrived as two commands. That
    escapes the readonly=True (EXAMINE) semantics this module deliberately
    chose, and desynchronises the tagged-response stream.

    Folder names arrive from a query parameter with no enumeration, and
    /api/fetch/proxy accepts an arbitrary host and credentials, so without this
    the server is an authenticated IMAP command relay pointed at third parties.
    """
    if not name:
        raise ValueError("empty mailbox name / 邮箱名为空")
    if len(name) > 255:
        raise ValueError("mailbox name too long / 邮箱名过长")
    if _MAILBOX_UNSAFE_RE.search(name):
        raise ValueError(f"invalid mailbox name / 非法邮箱名: {name!r}")
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _has_control_chars(*values: str | None) -> bool:
    """Any CR/LF/NUL in a credential would inject at the LOGIN stage."""
    return any(
        ch in (v or "") for v in values for ch in ("\r", "\n", "\x00")
    )


def _xoauth2_payload(email_addr: str, access_token: str) -> bytes:
    """RFC 7628 XOAUTH2 initial client response."""
    return f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


_UID_RE = re.compile(r"^[0-9]+$")


def _safe_uids(tokens: list[Any]) -> list[str]:
    """Accept only numeric IMAP UIDs; drop wildcards, ranges, and CRLF."""
    out: list[str] = []
    for tok in tokens:
        s = tok.decode() if isinstance(tok, (bytes, bytearray)) else str(tok)
        s = s.strip()
        if _UID_RE.fullmatch(s):
            out.append(s)
    return out


class _IMAP4SSLSni(imaplib.IMAP4_SSL):
    """IMAP4_SSL that verifies certificates against server_hostname, not connect IP.

    When SSRF pin rewrites the TCP peer to a public IP, stock IMAP4_SSL still sets
    server_hostname=host (the IP), which breaks real CA certs (Gmail, etc.).
    """

    def __init__(
        self,
        host: str = "",
        port: int = imaplib.IMAP4_SSL_PORT,
        *,
        timeout: float | None = None,
        ssl_context: Any = None,
        server_hostname: str | None = None,
        sock: Any = None,
    ) -> None:
        import ssl as _ssl

        self._tls_server_hostname = (server_hostname or host or "").strip() or host
        self._pre_sock = sock
        ctx = ssl_context or _ssl.create_default_context()
        # Parent stores ssl_context; open() uses host for TCP connect.
        super().__init__(host, port, timeout=timeout, ssl_context=ctx)

    def _create_socket(self, timeout):  # type: ignore[no-untyped-def]
        # IMAP4._create_socket → plain TCP to self.host (may be pinned IP)
        # Use __dict__: IMAP4.__getattr__ treats unknown names as IMAP commands.
        sock = self.__dict__.pop("_pre_sock", None)
        if sock is None:
            sock = imaplib.IMAP4._create_socket(self, timeout)
        assert self.ssl_context is not None
        return self.ssl_context.wrap_socket(
            sock,
            server_hostname=self._tls_server_hostname,
        )


class _IMAP4Sock(imaplib.IMAP4):
    """Plain IMAP4 that uses a pre-connected socket (SOCKS/HTTP CONNECT)."""

    def __init__(
        self,
        host: str = "",
        port: int = imaplib.IMAP4_PORT,
        *,
        timeout: float | None = None,
        sock: Any = None,
    ) -> None:
        self._pre_sock = sock
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):  # type: ignore[no-untyped-def]
        sock = self.__dict__.pop("_pre_sock", None)
        if sock is not None:
            return sock
        return super()._create_socket(timeout)


def _imap4_ssl_with_sni(
    connect_host: str,
    connect_port: int,
    *,
    server_hostname: str,
    timeout: float,
    sock: Any = None,
) -> imaplib.IMAP4:
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    try:
        return _IMAP4SSLSni(
            connect_host,
            connect_port,
            timeout=timeout,
            ssl_context=ctx,
            server_hostname=server_hostname,
            sock=sock,
        )
    except TypeError:
        # Extremely old Python — last resort without pin-aware SNI
        return imaplib.IMAP4_SSL(connect_host, connect_port, timeout=timeout)


def _provider_value(account: Any) -> str:
    p = getattr(account, "provider", None)
    if p is None:
        return ""
    return str(getattr(p, "value", p))


def normalize_imap_secret(password: str, email: str = "") -> str:
    """Normalize IMAP secrets (esp. Gmail app passwords pasted with spaces).

    Gmail app passwords are 16 letters, often shown as ``xxxx xxxx xxxx xxxx``.
    IMAP login requires the compact form without spaces.
    """
    pw = str(password or "").strip()
    if not pw:
        return pw
    compact = re.sub(r"\s+", "", pw)
    domain = ""
    if "@" in (email or ""):
        domain = email.rsplit("@", 1)[-1].strip().lower()
    # Always strip spaces for known Google domains
    if domain in ("gmail.com", "googlemail.com"):
        return compact
    # Pattern: four groups of 4 alnum (classic app-password display)
    if re.fullmatch(r"(?:[A-Za-z0-9]{4}\s+){3}[A-Za-z0-9]{4}", pw):
        return compact
    # 16-char app-password already compact
    return pw


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            for cs in (charset, "utf-8", "gb18030", "latin-1"):
                if not cs:
                    continue
                try:
                    out.append(chunk.decode(cs, errors="replace"))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
            else:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out).strip()


def _addr_list(header_val: str | None) -> tuple[str, str]:
    """Return (display, address) from a From/To header."""
    raw = _decode_header(header_val)
    if not raw:
        return "", ""
    name, addr = email.utils.parseaddr(raw)
    name = _decode_header(name) if name else ""
    display = raw if name or addr else raw
    if name and addr:
        display = f"{name} <{addr}>"
    elif addr:
        display = addr
    return display, (addr or "").lower()


def _part_payload_text(part: EmailPart) -> str:
    """Decode a text/* part with charset fallbacks used by CN / global ISPs."""
    # Prefer modern EmailMessage API when available
    if hasattr(part, "get_content"):
        try:
            content = part.get_content()  # type: ignore[attr-defined]
            if isinstance(content, str) and content.strip():
                return content
        except Exception:
            pass
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if payload is None:
        data = part.get_payload()
        if isinstance(data, list):
            return ""
        return data if isinstance(data, str) else ""
    if not isinstance(payload, (bytes, bytearray)):
        return str(payload)
    charset = (part.get_content_charset() or "").strip() or "utf-8"
    # QQ / 163 / 126 often declare gb2312/gbk; Gmail/Outlook utf-8
    for cs in (charset, "utf-8", "gb18030", "gbk", "gb2312", "big5", "latin-1"):
        try:
            return bytes(payload).decode(cs, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return bytes(payload).decode("utf-8", errors="replace")


def _looks_like_html(s: str) -> bool:
    if not s or "<" not in s:
        return False
    low = s.lstrip().lower()
    if low.startswith("<!doctype html") or low.startswith("<html"):
        return True
    return bool(
        re.search(
            r"<(?:html|body|div|p|table|br|span|font|center|h[1-6])\b",
            s,
            re.I,
        )
    )


def _html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_via_get_body(em: EmailPart) -> tuple[str, str] | None:
    """Official path: EmailMessage.get_body(preferencelist=…).

    See Python docs: https://docs.python.org/3/library/email.message.html
    RFC 2046 multipart/alternative: preferred part is last; get_body respects that.
    """
    if not hasattr(em, "get_body"):
        return None
    try:
        # Prefer HTML for rich UI; still collect plain separately
        html_part = em.get_body(preferencelist=("html",))  # type: ignore[attr-defined]
        plain_part = em.get_body(preferencelist=("plain",))  # type: ignore[attr-defined]
    except Exception:
        return None

    body_html = ""
    body_text = ""
    if html_part is not None:
        try:
            body_html = _part_payload_text(html_part).strip()
        except Exception:
            body_html = ""
    if plain_part is not None:
        try:
            body_text = _part_payload_text(plain_part).strip()
        except Exception:
            body_text = ""
    # If get_body returned something, trust it (even if one side empty)
    if body_html or body_text:
        if body_text and not body_html and _looks_like_html(body_text):
            body_html = body_text
        return body_text, body_html
    return None


def _extract_text_parts(em: EmailPart) -> tuple[str, str]:
    """Collect text/plain + text/html from MIME tree.

    1) Prefer EmailMessage.get_body (stdlib best practice).
    2) Fallback: recursive walk for nested mixed/related (QQ, 163, some gateways).
    """
    via = _extract_via_get_body(em)
    if via is not None:
        return via

    plains: list[str] = []
    htmls: list[str] = []

    def visit(part: EmailPart) -> None:
        ctype = (part.get_content_type() or "").lower()
        disp = str(part.get("Content-Disposition") or "").lower()
        maintype = (part.get_content_maintype() or "").lower()

        if part.is_multipart() or maintype == "multipart":
            try:
                children = part.get_payload()
            except Exception:
                children = None
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, (EmailMessage, CompatMessage)):
                        visit(child)
            return

        if "attachment" in disp:
            if "inline" not in disp and ctype not in ("text/html", "text/plain"):
                return

        if ctype == "text/plain":
            t = _part_payload_text(part).strip()
            if t:
                plains.append(t)
        elif ctype in ("text/html", "text/x-amp-html", "application/xhtml+xml"):
            t = _part_payload_text(part).strip()
            if t:
                htmls.append(t)
        elif ctype.startswith("text/") and not ctype.startswith("text/calendar"):
            t = _part_payload_text(part).strip()
            if not t:
                return
            if _looks_like_html(t):
                htmls.append(t)
            else:
                plains.append(t)

    visit(em)

    # Longest non-empty wins (RFC 2046 alternative: richest/last often longest HTML)
    body_text = max(plains, key=len) if plains else ""
    body_html = max(htmls, key=len) if htmls else ""
    if body_text and not body_html and _looks_like_html(body_text):
        body_html = body_text
    return body_text, body_html


def parse_rfc822(raw: bytes | str, *, msg_id: str, folder: str = "inbox") -> Message:
    """Parse RFC822 into Message for UI (both plain + HTML).

    Aligns with:
    - RFC 2046 multipart/alternative (prefer richest/last part for display)
    - Python email.EmailMessage.get_body(preferencelist=('html'|'plain'))
    - Common open-source pattern (mailparser: keep both ``html`` and ``text``)

    Mainstream layouts: alternative, related, mixed nesting (QQ/163/Gmail/Outlook),
    single-part html/plain, gb18030/gbk/utf-8 charsets.
    """
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8", errors="replace")
    else:
        raw_bytes = raw
    # policy.default → EmailMessage with get_body / get_content
    try:
        em = email.message_from_bytes(raw_bytes, policy=email_policy.default)
    except Exception:
        em = email.message_from_bytes(raw_bytes)

    subject = _decode_header(em.get("Subject"))
    from_disp, from_addr = _addr_list(em.get("From"))
    to_disp, _ = _addr_list(em.get("To"))
    date_hdr = em.get("Date")
    date_str = date_hdr.strip() if isinstance(date_hdr, str) else (
        str(date_hdr).strip() if date_hdr else None
    )

    body_text, body_html = _extract_text_parts(em)

    # Prefer rich plain for code extraction when HTML is fuller (common CN stubs)
    if body_html:
        from_html = _html_to_text(body_html)
        if not body_text or (from_html and len(from_html) >= len(body_text)):
            body_text = from_html or body_text

    preview_src = body_text or _html_to_text(body_html or "")
    preview = re.sub(r"\s+", " ", preview_src).strip()[:280]

    msg = Message(
        id=str(msg_id),
        subject=subject,
        from_=from_disp,
        from_address=from_addr,
        to=to_disp,
        date=date_str,
        body_preview=preview,
        body_text=body_text,
        body_html=body_html,
        folder=folder,
    )
    attach_verification_code([msg])
    return msg


class ImapProvider:
    """Real IMAP fetch provider (stdlib imaplib)."""

    name = "imap"
    time_paging = "since_before"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def can_handle(self, account: Any) -> bool:
        return _provider_value(account) == "imap"

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
        password = (
            creds.get("password")
            or creds.get("auth_code")
            or creds.get("password_plain")
            or getattr(account, "password", None)
            or ""
        )
        password = normalize_imap_secret(str(password), email_addr)
        access_token = str(creds.get("access_token") or creds.get("oauth_access_token") or "").strip()
        if not email_addr:
            return FetchResult(ok=False, folder=folder, error="缺少邮箱地址")
        if not password and not access_token:
            return FetchResult(ok=False, folder=folder, error="缺少 IMAP 密码或授权码")

        try:
            hint = resolve_imap_host(
                email_addr,
                imap_host=creds.get("imap_host") or creds.get("host"),
                imap_port=creds.get("imap_port") or creds.get("port"),
                imap_ssl=creds.get("imap_ssl") if "imap_ssl" in creds else creds.get("ssl"),
            )
        except ValueError as exc:
            return FetchResult(ok=False, folder=folder, error=str(exc))

        limit = QUICK_LIMIT if quick else FULL_LIMIT
        if limits and "max_messages" in limits:
            try:
                limit = max(1, min(int(limits["max_messages"]), 100))
            except (TypeError, ValueError):
                pass

        conn: imaplib.IMAP4 | None = None
        try:
            proxy = str(creds.get("proxy") or getattr(account, "proxy", None) or "").strip() or None
            conn = self._login_connect(
                hint.host,
                hint.port,
                hint.ssl,
                email_addr,
                password,
                proxy=proxy,
                access_token=access_token or None,
            )
            if conn is None:
                return FetchResult(ok=False, folder=folder, error="IMAP 登录失败")

            selected, select_err = self._select_folder(conn, folder)
            if selected is None:
                detail = select_err or f"无法打开文件夹 {folder}"
                if "unsafe login" in detail.lower():
                    detail = (
                        "网易邮箱拒绝取信（Unsafe Login）：请确认已用客户端授权码，"
                        "且 OpenMail 已发送 IMAP ID。若仍失败请稍后重试 / "
                        "NetEase Unsafe Login — use app password; IMAP ID required"
                    )
                return FetchResult(ok=False, folder=folder, error=detail)

            uidvalidity = self._read_uidvalidity(conn)

            since = None
            before = None
            if limits:
                since = limits.get("since") or limits.get("received_after")
                before = limits.get("before") or limits.get("received_before")
            if before:
                uids = self._uids_before(conn, str(before), limit)
            elif since:
                uids = self._uids_since(conn, str(since), limit)
            else:
                uids = self._recent_uids(conn, limit)
            messages: list[Message] = []
            folder_out = folder.lower()
            since_dt: datetime | None = None
            before_dt: datetime | None = None
            for bound, attr in ((since, "since"), (before, "before")):
                if not bound:
                    continue
                try:
                    dt = datetime.fromisoformat(str(bound).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if attr == "since":
                        since_dt = dt
                    else:
                        before_dt = dt
                except (TypeError, ValueError):
                    pass
            for uid in uids:
                raw = self._fetch_rfc822(conn, uid)
                if raw is None:
                    continue
                msg = parse_rfc822(raw, msg_id=str(uid), folder=folder_out)
                if since_dt is not None or before_dt is not None:
                    try:
                        msg_dt = email.utils.parsedate_to_datetime(msg.date)
                        if msg_dt is None:
                            msg_dt = datetime.fromisoformat(str(msg.date).replace("Z", "+00:00"))
                        if msg_dt.tzinfo is None:
                            msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                        if since_dt is not None and msg_dt < since_dt:
                            continue
                        if before_dt is not None and msg_dt >= before_dt:
                            continue
                    except (TypeError, ValueError, OverflowError, IndexError):
                        # A bounded query cannot safely place an unparseable date.
                        continue
                if uidvalidity is not None:
                    msg.uidvalidity = uidvalidity
                messages.append(msg)
                if before and len(messages) >= limit:
                    break

            attach_verification_code(messages)
            return FetchResult(
                ok=True,
                messages=messages,
                folder=folder_out,
                session_restored=False,
                uidvalidity=uidvalidity,
            )
        except imaplib.IMAP4.error as exc:
            return FetchResult(ok=False, folder=folder, error=self._map_imap_error(exc))
        except (socket.timeout, TimeoutError, OSError) as exc:
            return FetchResult(ok=False, folder=folder, error=f"IMAP 连接超时或网络错误: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface short error
            return FetchResult(ok=False, folder=folder, error=f"IMAP 取信失败: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    try:
                        conn.shutdown()
                    except Exception:
                        pass

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        result = self.fetch(
            account,
            folder="inbox",
            quick=True,
            limits={"max_messages": 1},
            credentials=credentials,
        )
        if result.ok:
            return HealthResult(ok=True, detail="imap ok")
        return HealthResult(ok=False, detail=result.error)

    # --- internals ---

    def _connect(
        self,
        host: str,
        port: int,
        use_ssl: bool,
        *,
        server_hostname: str | None = None,
        proxy: str | None = None,
    ) -> imaplib.IMAP4:
        """Connect to host (may be pinned IP). server_hostname used for TLS SNI/cert.

        SSRF mitigation resolves the hostname to a public IP and connects to that
        IP. Certificate verification must still use the original hostname
        (e.g. imap.gmail.com), otherwise TLS fails with hostname/IP mismatch
        (often reported as CERTIFICATE_VERIFY_FAILED / self-signed).
        When *proxy* is set, TCP goes through SOCKS5/HTTP CONNECT to the pinned IP.
        """
        from app.services.ssrf import resolve_mail_endpoint

        connect_host, connect_port, sni = resolve_mail_endpoint(
            host, port, use_ssl=use_ssl
        )
        tls_name = (server_hostname or sni or host).strip()
        sock = None
        proxy_url = (proxy or "").strip() or None
        if proxy_url:
            from app.services.tcp_proxy import open_proxied_tcp

            sock = open_proxied_tcp(
                proxy_url, connect_host, connect_port, timeout=self.timeout
            )
        if use_ssl:
            return _imap4_ssl_with_sni(
                connect_host,
                connect_port,
                server_hostname=tls_name,
                timeout=self.timeout,
                sock=sock,
            )
        conn: imaplib.IMAP4
        if sock is not None:
            conn = _IMAP4Sock(
                connect_host, connect_port, timeout=self.timeout, sock=sock
            )
        else:
            conn = imaplib.IMAP4(connect_host, connect_port, timeout=self.timeout)
        try:
            typ, _ = conn.starttls()
        except Exception as exc:
            try:
                conn.logout()
            except Exception:
                pass
            raise RuntimeError(
                f"IMAP STARTTLS failed / IMAP 未能升级到 TLS: {exc}"
            ) from exc
        if typ != "OK":
            try:
                conn.logout()
            except Exception:
                pass
            raise RuntimeError("IMAP STARTTLS rejected / 服务器拒绝 STARTTLS")
        return conn

    def _login_connect(
        self,
        host: str,
        port: int,
        use_ssl: bool,
        email_addr: str,
        password: str,
        *,
        proxy: str | None = None,
        access_token: str | None = None,
    ) -> imaplib.IMAP4 | None:
        """Connect and login; try full email then local-part (iCloud)."""
        # imaplib sends LOGIN's username unquoted, and `_quote()` on the password
        # escapes only `\` and `"` — neither handles CRLF, so a credential with
        # control characters injects a command before authentication completes.
        token = (access_token or "").strip()
        if _has_control_chars(email_addr, password, token):
            raise ValueError("credential contains control characters / 凭据包含控制字符")

        candidates = [email_addr]
        if "@" in email_addr:
            local = email_addr.split("@", 1)[0].strip()
            if local and local not in candidates:
                candidates.append(local)

        last_err: Exception | None = None
        for user in candidates:
            conn: imaplib.IMAP4 | None = None
            try:
                conn = self._connect(
                    host, port, use_ssl, server_hostname=host, proxy=proxy
                )
                if token:
                    typ, _ = conn.authenticate(
                        "XOAUTH2",
                        lambda _challenge, u=user, t=token: _xoauth2_payload(u, t),
                    )
                else:
                    typ, _ = conn.login(user, password)
                if typ == "OK":
                    # NetEase and other ID-capable servers: identify client after login
                    self._send_client_id(conn, host=host)
                    return conn
                try:
                    conn.logout()
                except Exception:
                    pass
            except RuntimeError:
                raise
            except imaplib.IMAP4.error as exc:
                last_err = exc
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:
                        pass
            except Exception as exc:
                last_err = exc
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:
                        pass
        if last_err:
            raise last_err
        return None

    def _send_client_id(self, conn: imaplib.IMAP4, *, host: str = "") -> None:
        """Send IMAP ID (RFC 2971) after LOGIN when the server expects it.

        NetEase (126/163/yeah Coremail) returns SELECT NO
        ``EXAMINE Unsafe Login. Please contact kefu@188.com`` if ID is missing.
        Harmless no-op on servers that ignore ID.
        """
        host_l = (host or "").strip().lower()
        force = any(m in host_l for m in _NETEASE_IMAP_HOST_MARKERS)
        cap_has_id = False
        try:
            # Prefer already-cached capabilities from greeting/login
            caps = getattr(conn, "capabilities", ()) or ()
            cap_blob = " ".join(
                c.decode(errors="replace") if isinstance(c, (bytes, bytearray)) else str(c)
                for c in (caps if not isinstance(caps, (str, bytes, bytearray)) else [caps])
            ).upper()
            if "ID" in cap_blob.split() or " ID " in f" {cap_blob} ":
                cap_has_id = True
            if not cap_has_id:
                typ, data = conn.capability()
                if typ == "OK" and data:
                    for item in data:
                        s = item.decode(errors="replace") if isinstance(item, (bytes, bytearray)) else str(item)
                        if "ID" in s.upper().split():
                            cap_has_id = True
                            break
        except Exception:
            cap_has_id = force

        if not force and not cap_has_id:
            return

        # imaplib does not register ID by default
        try:
            imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
        except Exception:
            pass

        # Quoted string list: ("name" "OpenMail" "version" "1.0" ...)
        id_args = (
            '("name" "OpenMail" "version" "1.0" '
            '"vendor" "OpenMail" "support-email" "support@openmail.local")'
        )
        try:
            typ, _ = conn._simple_command("ID", id_args)  # type: ignore[attr-defined]
            if typ != "OK" and force:
                # Fallback raw send (some imaplib builds are picky about args)
                tag = conn._new_tag()  # type: ignore[attr-defined]
                conn.send(tag + b" ID " + id_args.encode("ascii") + b"\r\n")  # type: ignore[attr-defined]
                while True:
                    line = conn.readline()  # type: ignore[attr-defined]
                    if not line or line.startswith(tag) or line.startswith(b"* BYE"):
                        break
        except Exception:
            if not force:
                return
            try:
                tag = conn._new_tag()  # type: ignore[attr-defined]
                conn.send(  # type: ignore[attr-defined]
                    tag + b' ID ("name" "OpenMail" "version" "1.0" "vendor" "OpenMail")\r\n'
                )
                while True:
                    line = conn.readline()  # type: ignore[attr-defined]
                    if not line or line.startswith(tag) or line.startswith(b"* BYE"):
                        break
            except Exception:
                pass

    def _read_uidvalidity(self, conn: imaplib.IMAP4) -> int | None:
        """Parse UIDVALIDITY from untagged SELECT response (or STATUS)."""
        import re

        def _parse_vals(vals: Any) -> int | None:
            if isinstance(vals, (bytes, bytearray, str)):
                vals = [vals]
            for item in vals or []:
                try:
                    if isinstance(item, (bytes, bytearray)):
                        return int(item)
                    if isinstance(item, str):
                        return int(item.strip())
                    if isinstance(item, (list, tuple)) and item:
                        return int(item[0])
                    return int(item)
                except (TypeError, ValueError, IndexError):
                    continue
            return None

        try:
            untagged = getattr(conn, "untagged_responses", {}) or {}
            for key, vals in untagged.items():
                key_name = key.decode(errors="replace") if isinstance(key, bytes) else str(key)
                if key_name.upper() == "UIDVALIDITY":
                    n = _parse_vals(vals)
                    if n is not None:
                        return n
            # Some test doubles and older imaplib variants expose responses via
            # response() rather than untagged_responses.
            response = getattr(conn, "response", None)
            if callable(response):
                typ, vals = response("UIDVALIDITY")
                n = _parse_vals(vals)
                if n is not None:
                    return n
            folder_name = getattr(conn, "_current_folder", None) or "INBOX"
            typ, data = conn.status(_quote_mailbox(str(folder_name)), "(UIDVALIDITY)")
            if typ == "OK" and data:
                blob = data[0]
                if isinstance(blob, (bytes, bytearray)):
                    blob = blob.decode("utf-8", errors="replace")
                m = re.search(r"UIDVALIDITY\s+(\d+)", str(blob), re.I)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None

    def _sequence_to_uids(
        self, conn: imaplib.IMAP4, seqs: list[bytes | str]
    ) -> list[str]:
        """Convert sequence numbers returned by non-UID SEARCH to stable UIDs."""
        out: list[str] = []
        for seq in seqs:
            token = seq.decode() if isinstance(seq, bytes) else str(seq)
            try:
                typ, data = conn.fetch(token, "(UID)")
                blob = b""
                if typ == "OK" and data:
                    for item in data:
                        part = item[0] if isinstance(item, tuple) and item else item
                        if isinstance(part, str):
                            part = part.encode()
                        if isinstance(part, (bytes, bytearray)):
                            blob += bytes(part)
                    match = re.search(rb"\bUID\s+(\d+)", blob, re.I)
                    if match:
                        out.append(match.group(1).decode())
                        continue
            except Exception:
                pass
            # Preserve the token as a last resort for permissive test servers.
            out.append(token)
        return out

    def _select_folder(self, conn: imaplib.IMAP4, folder: str) -> tuple[str | None, str | None]:
        """Return (selected_name, error_detail). error_detail set when all SELECT fail."""
        folder_l = (folder or "inbox").lower()
        candidates: list[str]
        if folder_l in ("inbox", "in", "收件箱"):
            candidates = list(INBOX_ALIASES)
        elif folder_l in ("junk", "spam", "junkemail", "垃圾", "垃圾邮件"):
            candidates = list(JUNK_CANDIDATES)
        elif folder_l in ("sent", "sentitems", "sent mail", "已发送", "已发"):
            # Prefer ASCII IMAP names first; Chinese aliases only after LIST/UTF-7 encode
            candidates = list(SENT_CANDIDATES)
        else:
            candidates = [folder, folder.upper(), folder.capitalize()]

        # Probe LIST for junk / sent aliases when those folders are requested
        if folder_l in ("junk", "spam", "junkemail", "垃圾", "垃圾邮件"):
            listed = self._list_mailbox_names(conn)
            for name in listed:
                low = _imap_utf7_decode(name).lower()
                if any(k in low for k in ("junk", "spam", "bulk", "垃圾")):
                    if name not in candidates:
                        candidates.append(name)
        elif folder_l in ("sent", "sentitems", "sent mail", "已发送", "已发"):
            listed = self._list_mailbox_names(conn)
            for name in listed:
                decoded = _imap_utf7_decode(name)
                low = decoded.lower()
                if any(k in low for k in ("sent", "已发送", "已发")):
                    if name not in candidates:
                        candidates.append(name)
                    if decoded not in candidates:
                        candidates.append(decoded)

        # Prefer pure-ASCII candidates first so Gmail/Outlook never hit 已发送 raw
        candidates.sort(key=lambda n: (0 if all(ord(c) < 128 for c in n) else 1, n))

        last_no: str | None = None
        for name in candidates:
            for variant in _mailbox_select_variants(name):
                try:
                    quoted = _quote_mailbox(variant)
                except ValueError as exc:
                    last_no = str(exc)
                    continue
                try:
                    typ, data = conn.select(quoted, readonly=True)
                    if typ == "OK":
                        try:
                            conn._current_folder = variant  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        return variant, None
                    # Preserve server NO text (e.g. NetEase Unsafe Login)
                    if data:
                        blob = data[0] if isinstance(data, (list, tuple)) and data else data
                        if isinstance(blob, (bytes, bytearray)):
                            last_no = blob.decode("utf-8", errors="replace")
                        else:
                            last_no = str(blob)
                except imaplib.IMAP4.error as exc:
                    last_no = str(exc)
                    continue
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
                except Exception:
                    continue
        err = last_no or f"无法打开文件夹 {folder}"
        if last_no and "无法打开" not in last_no and "Unsafe" not in last_no:
            err = f"无法打开文件夹 {folder}: {last_no}"
        elif last_no and "Unsafe" in last_no:
            err = last_no
        return None, err

    def _list_mailbox_names(self, conn: imaplib.IMAP4) -> list[str]:
        names: list[str] = []
        try:
            typ, data = conn.list()
            if typ != "OK" or not data:
                return names
            for item in data:
                if not item:
                    continue
                line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
                # LIST reply: (flags) "delim" name
                m = re.search(r' "([^"]*)"$| ([^\s"]+)$', line)
                if m:
                    names.append(m.group(1) or m.group(2))
                else:
                    # fallback: last token
                    parts = line.rsplit(" ", 1)
                    if len(parts) == 2:
                        names.append(parts[1].strip('"'))
        except Exception:
            return names
        return names

    @staticmethod
    def _imap_day(iso: str, *, end_of_day: bool = False) -> str:
        """Parse ISO → IMAP date token. end_of_day: bump +1 day for BEFORE exclusive."""
        from datetime import datetime, timedelta

        day = "01-Jan-1970"
        try:
            s = str(iso).replace("Z", "+00:00")
            if "T" in s:
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.fromisoformat(s + "T00:00:00")
            if end_of_day:
                # IMAP BEFORE is exclusive on calendar day; keep same day when time present
                # so we still get earlier mails on that day via post-filter if needed.
                pass
            day = dt.strftime("%d-%b-%Y")
        except Exception:
            pass
        return day

    def _uids_since(self, conn: imaplib.IMAP4, since: str, limit: int) -> list[str]:
        """IMAP SEARCH SINCE date — only messages on/after calendar day of `since`."""
        day = self._imap_day(since)
        try:
            typ, data = conn.uid("search", None, "SINCE", day)
        except imaplib.IMAP4.error:
            try:
                typ, data = conn.search(None, "SINCE", day)
            except Exception:
                return self._recent_uids(conn, limit)
            if typ != "OK" or not data or not data[0]:
                return []
            seqs = data[0].split()[-limit:]
            seqs.reverse()
            return self._sequence_to_uids(conn, seqs)
        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = data[0].split()[-limit:]
        all_uids.reverse()
        return _safe_uids(all_uids)

    def _uids_before(self, conn: imaplib.IMAP4, before: str, limit: int) -> list[str]:
        """Load older page: messages strictly before `before` (newest-first slice of older set).

        IMAP BEFORE is calendar-day exclusive. We search BEFORE (day+1) then take the
        last `limit` UIDs (oldest-of-recent / newest-of-older window) and reverse to newest first.
        """
        from datetime import datetime, timedelta

        day = "01-Jan-1970"
        try:
            s = str(before).replace("Z", "+00:00")
            if "T" in s:
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.fromisoformat(s + "T00:00:00")
            # BEFORE next calendar day so same-day earlier mails can still appear
            day = (dt + timedelta(days=1)).strftime("%d-%b-%Y")
        except Exception:
            day = self._imap_day(before)

        # Cap candidates so a large mailbox cannot force unbounded UID lists
        # (and subsequent RFC822 fetches) when client-side date filtering skips
        # many messages. Budget scales with page size but stays hard-capped.
        candidate_budget = max(limit * 10, 50)
        candidate_budget = min(candidate_budget, 500)
        try:
            typ, data = conn.uid("search", None, "BEFORE", day)
        except imaplib.IMAP4.error:
            try:
                typ, data = conn.search(None, "BEFORE", day)
            except Exception:
                return []
            if typ != "OK" or not data or not data[0]:
                return []
            seqs = data[0].split()
            # Newest-of-older window: take the rightmost slice before reverse.
            seqs = seqs[-candidate_budget:]
            seqs.reverse()
            return self._sequence_to_uids(conn, seqs)
        if typ != "OK" or not data or not data[0]:
            return []
        # BEFORE only has day precision. Return a bounded newest-first candidate
        # window; fetch() applies the exact timestamp and stops after `limit`.
        all_uids = data[0].split()
        all_uids = all_uids[-candidate_budget:]
        all_uids.reverse()
        return _safe_uids(all_uids)

    def _recent_uids(self, conn: imaplib.IMAP4, limit: int) -> list[str]:
        try:
            typ, data = conn.uid("search", None, "ALL")
        except imaplib.IMAP4.error:
            typ, data = conn.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            seqs = data[0].split()
            seqs = seqs[-limit:]
            seqs.reverse()  # newest first if server lists ascending
            # fetch by sequence — caller uses uid path; convert via FETCH UID
            uids: list[str] = []
            for seq in seqs:
                try:
                    t2, d2 = conn.fetch(seq, "(UID)")
                    if t2 == "OK" and d2 and d2[0]:
                        m = re.search(rb"UID\s+(\d+)", d2[0][0] if isinstance(d2[0], tuple) else d2[0])
                        if m:
                            uids.append(m.group(1).decode())
                            continue
                except Exception:
                    pass
                uids.append(seq.decode() if isinstance(seq, bytes) else str(seq))
            return _safe_uids(uids)

        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = data[0].split()
        recent = all_uids[-limit:]
        recent.reverse()  # newest first
        return _safe_uids(recent)

    def _fetch_rfc822(self, conn: imaplib.IMAP4, uid: str) -> bytes | None:
        if not _UID_RE.fullmatch(str(uid).strip()):
            return None
        try:
            typ, data = conn.uid("fetch", uid, "(RFC822)")
        except imaplib.IMAP4.error:
            typ, data = conn.fetch(uid, "(RFC822)")
        if typ != "OK" or not data:
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return None

    @staticmethod
    def _map_imap_error(exc: BaseException) -> str:
        text = str(exc).lower()
        if "authentication" in text or "login" in text or "invalid credentials" in text:
            return "IMAP 认证失败，请检查授权码"
        if "timeout" in text:
            return "IMAP 连接超时"
        return f"IMAP 错误: {exc}"
