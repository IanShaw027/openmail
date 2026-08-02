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
from email.message import Message as EmailMessage
from typing import Any

from app.providers.base import FetchResult, HealthResult, Message
from app.providers.imap_hosts import resolve_imap_host
from app.services.parser import annotate_message_code, attach_verification_code

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


def _imap_utf7_encode(name: str) -> str:
    """RFC 3501 modified UTF-7 for mailbox names (non-ASCII → &...-)."""
    if not name:
        return name
    try:
        name.encode("ascii")
        return name
    except UnicodeEncodeError:
        pass
    # Standard utf-7 then map +…- → &…- and unescape &
    encoded = name.encode("utf-7").decode("ascii")
    return encoded.replace("+", "&").replace("&-", "&")


def _imap_utf7_decode(name: str) -> str:
    """Decode modified UTF-7 mailbox name for matching."""
    if not name or "&" not in name:
        return name
    try:
        std = name.replace("&", "+")
        # '&' alone as literal was '&-'; after replace becomes '+-' which utf-7 treats as '+'
        return std.encode("ascii").decode("utf-7")
    except Exception:
        return name


def _mailbox_select_variants(name: str) -> list[str]:
    """Names to try with IMAP SELECT (raw + modified UTF-7)."""
    out: list[str] = []
    for n in (name, _imap_utf7_encode(name)):
        if n and n not in out:
            out.append(n)
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
    ) -> None:
        import ssl as _ssl

        self._tls_server_hostname = (server_hostname or host or "").strip() or host
        ctx = ssl_context or _ssl.create_default_context()
        # Parent stores ssl_context; open() uses host for TCP connect.
        super().__init__(host, port, timeout=timeout, ssl_context=ctx)

    def _create_socket(self, timeout):  # type: ignore[no-untyped-def]
        # IMAP4._create_socket → plain TCP to self.host (may be pinned IP)
        sock = imaplib.IMAP4._create_socket(self, timeout)
        assert self.ssl_context is not None
        return self.ssl_context.wrap_socket(
            sock,
            server_hostname=self._tls_server_hostname,
        )


def _imap4_ssl_with_sni(
    connect_host: str,
    connect_port: int,
    *,
    server_hostname: str,
    timeout: float,
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


def _part_payload_text(part: EmailMessage) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None
    if payload is None:
        data = part.get_payload()
        return data if isinstance(data, str) else ""
    if not isinstance(payload, (bytes, bytearray)):
        return str(payload)
    charset = part.get_content_charset() or "utf-8"
    for cs in (charset, "utf-8", "gb18030", "latin-1"):
        try:
            return bytes(payload).decode(cs, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return bytes(payload).decode("utf-8", errors="replace")


def parse_rfc822(raw: bytes | str, *, msg_id: str, folder: str = "inbox") -> Message:
    """Parse RFC822 bytes into provider Message."""
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8", errors="replace")
    else:
        raw_bytes = raw
    em = email.message_from_bytes(raw_bytes)

    subject = _decode_header(em.get("Subject"))
    from_disp, from_addr = _addr_list(em.get("From"))
    to_disp, _ = _addr_list(em.get("To"))
    date_hdr = em.get("Date")
    date_str = date_hdr.strip() if date_hdr else None

    body_text = ""
    body_html = ""
    if em.is_multipart():
        for part in em.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and not body_text:
                body_text = _part_payload_text(part)
            elif ctype == "text/html" and not body_html:
                body_html = _part_payload_text(part)
    else:
        ctype = (em.get_content_type() or "text/plain").lower()
        text = _part_payload_text(em)
        if ctype == "text/html":
            body_html = text
        else:
            body_text = text

    preview_src = body_text or re.sub(r"<[^>]+>", " ", body_html or "")
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
        if not email_addr:
            return FetchResult(ok=False, folder=folder, error="缺少邮箱地址")
        if not password:
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
            conn = self._login_connect(hint.host, hint.port, hint.ssl, email_addr, password)
            if conn is None:
                return FetchResult(ok=False, folder=folder, error="IMAP 登录失败")

            selected = self._select_folder(conn, folder)
            if selected is None:
                return FetchResult(ok=False, folder=folder, error=f"无法打开文件夹 {folder}")

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
            for uid in uids:
                raw = self._fetch_rfc822(conn, uid)
                if raw is None:
                    continue
                messages.append(parse_rfc822(raw, msg_id=str(uid), folder=folder.lower()))

            attach_verification_code(messages)
            return FetchResult(
                ok=True,
                messages=messages,
                folder=folder.lower(),
                session_restored=False,
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
    ) -> imaplib.IMAP4:
        """Connect to host (may be pinned IP). server_hostname used for TLS SNI/cert.

        SSRF mitigation resolves the hostname to a public IP and connects to that
        IP. Certificate verification must still use the original hostname
        (e.g. imap.gmail.com), otherwise TLS fails with hostname/IP mismatch
        (often reported as CERTIFICATE_VERIFY_FAILED / self-signed).
        """
        from app.services.ssrf import resolve_mail_endpoint

        connect_host, connect_port, sni = resolve_mail_endpoint(
            host, port, use_ssl=use_ssl
        )
        tls_name = (server_hostname or sni or host).strip()
        socket.setdefaulttimeout(self.timeout)
        if use_ssl:
            return _imap4_ssl_with_sni(
                connect_host,
                connect_port,
                server_hostname=tls_name,
                timeout=self.timeout,
            )
        return imaplib.IMAP4(connect_host, connect_port, timeout=self.timeout)

    def _login_connect(
        self,
        host: str,
        port: int,
        use_ssl: bool,
        email_addr: str,
        password: str,
    ) -> imaplib.IMAP4 | None:
        """Connect and login; try full email then local-part (iCloud)."""
        candidates = [email_addr]
        if "@" in email_addr:
            local = email_addr.split("@", 1)[0].strip()
            if local and local not in candidates:
                candidates.append(local)

        last_err: Exception | None = None
        for user in candidates:
            conn: imaplib.IMAP4 | None = None
            try:
                conn = self._connect(host, port, use_ssl, server_hostname=host)
                typ, _ = conn.login(user, password)
                if typ == "OK":
                    return conn
                try:
                    conn.logout()
                except Exception:
                    pass
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

    def _select_folder(self, conn: imaplib.IMAP4, folder: str) -> str | None:
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

        for name in candidates:
            for variant in _mailbox_select_variants(name):
                try:
                    typ, _ = conn.select(variant, readonly=True)
                    if typ == "OK":
                        return variant
                except (imaplib.IMAP4.error, UnicodeEncodeError, UnicodeDecodeError):
                    continue
                except Exception:
                    continue
        return None

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
            return [x.decode() if isinstance(x, bytes) else str(x) for x in seqs]
        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = data[0].split()[-limit:]
        all_uids.reverse()
        return [u.decode() if isinstance(u, bytes) else str(u) for u in all_uids]

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
            # ascending: take last limit (newest among older set)
            seqs = seqs[-limit:] if len(seqs) > limit else seqs
            seqs.reverse()
            return [x.decode() if isinstance(x, bytes) else str(x) for x in seqs]
        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = data[0].split()
        recent = all_uids[-limit:] if len(all_uids) > limit else all_uids
        recent.reverse()
        return [u.decode() if isinstance(u, bytes) else str(u) for u in recent]

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
            return uids

        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = data[0].split()
        recent = all_uids[-limit:]
        recent.reverse()  # newest first
        return [
            u.decode() if isinstance(u, bytes) else str(u) for u in recent
        ]

    def _fetch_rfc822(self, conn: imaplib.IMAP4, uid: str) -> bytes | None:
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
