"""Send mail via Microsoft Graph (OAuth) or SMTP (IMAP-class accounts).

After successful SMTP send, best-effort IMAP APPEND into the Sent folder so the
Console 「发件箱」 tab can show outbound mail without waiting for provider-side copies.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

import httpx

from app.providers.imap_provider import ImapProvider, normalize_imap_secret
from app.providers.oauth_graph import DEFAULT_SCOPE, OAuthError, OAuthGraphProvider
from app.providers.smtp_hosts import resolve_smtp_host

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Prefer Mail.Send when available; fall back to Mail.Read offline if app only has read
SEND_SCOPE = "https://graph.microsoft.com/Mail.Send offline_access Mail.Read"


@dataclass
class SendResult:
    ok: bool
    error: str | None = None
    detail: str | None = None
    # True when SMTP message was also APPENDed to IMAP Sent (best-effort)
    saved_to_sent: bool = False


def _provider_value(account: Any) -> str:
    p = getattr(account, "provider", None)
    if p is None:
        return ""
    return str(getattr(p, "value", p))


def send_via_graph(
    *,
    client_id: str,
    refresh_token: str,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    proxy: str | None = None,
) -> SendResult:
    if not client_id or not refresh_token:
        return SendResult(ok=False, error="缺少 client_id 或 refresh_token")
    if not to:
        return SendResult(ok=False, error="收件人不能为空")

    provider = OAuthGraphProvider()
    try:
        # Try Mail.Send scope first; some apps only registered Mail.Read — still try send
        try:
            token_body = provider.refresh_access_token(
                client_id=client_id,
                refresh_token=refresh_token,
                scope=SEND_SCOPE,
                proxy=proxy,
            )
        except OAuthError:
            token_body = provider.refresh_access_token(
                client_id=client_id,
                refresh_token=refresh_token,
                scope=DEFAULT_SCOPE,
                proxy=proxy,
            )
    except OAuthError as exc:
        return SendResult(ok=False, error=str(exc))
    except httpx.HTTPError as exc:
        return SendResult(ok=False, error=f"网络错误: {exc.__class__.__name__}")

    access_token = str(token_body["access_token"])
    content_type = "HTML" if body_html else "Text"
    content = body_html if body_html else body_text
    payload = {
        "message": {
            "subject": subject or "(no subject)",
            "body": {"contentType": content_type, "content": content or ""},
            "toRecipients": [
                {"emailAddress": {"address": addr.strip()}} for addr in to if addr.strip()
            ],
        },
        "saveToSentItems": True,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=30.0, proxy=proxy) as client:
            resp = client.post(f"{GRAPH_BASE}/me/sendMail", headers=headers, json=payload)
        if resp.status_code in (200, 202):
            return SendResult(ok=True, detail="sent via graph")
        if resp.status_code in (401, 403):
            return SendResult(
                ok=False,
                error="Graph 无发信权限，请确认应用已授权 Mail.Send / Graph Mail.Send not granted",
            )
        return SendResult(ok=False, error=f"Graph 发信失败 ({resp.status_code})")
    except httpx.HTTPError as exc:
        return SendResult(ok=False, error=f"网络错误: {exc.__class__.__name__}")


def _build_outbound_message(
    *,
    email_addr: str,
    recipients: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "(no subject)"
    if not msg.get("Date"):
        msg["Date"] = formatdate(localtime=True)
    if body_html:
        msg.set_content(body_text or "")
        msg.add_alternative(body_html, subtype="html")
    else:
        msg.set_content(body_text or "")
    return msg


def append_to_sent_imap(
    *,
    email_addr: str,
    password: str,
    raw_message: bytes | EmailMessage,
    imap_host: str | None = None,
    imap_port: int | None = None,
    imap_ssl: bool | None = None,
) -> tuple[bool, str | None]:
    """APPEND a just-sent message into the Sent mailbox (best-effort).

    Returns (ok, error_or_None). Failures are soft: SMTP already succeeded.
    """
    if not email_addr or not password:
        return False, "missing credentials"
    if isinstance(raw_message, EmailMessage):
        raw = raw_message.as_bytes()
    else:
        raw = bytes(raw_message)
    if not raw:
        return False, "empty message"

    provider = ImapProvider(timeout=25.0)
    conn: imaplib.IMAP4 | None = None
    try:
        from app.providers.imap_hosts import resolve_imap_host

        hint = resolve_imap_host(
            email_addr,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_ssl=imap_ssl,
        )
        conn = provider._login_connect(  # noqa: SLF001 — shared login + NetEase ID
            hint.host, hint.port, hint.ssl, email_addr, password
        )
        if conn is None:
            return False, "IMAP login failed"

        selected, select_err = provider._select_folder(conn, "sent")  # noqa: SLF001
        if not selected:
            return False, select_err or "no sent folder"

        # APPEND requires a mailbox name; some servers want SELECT first (done above).
        # Flags: \\Seen so it doesn't look unread in Sent.
        now = imaplib.Time2Internaldate(time.time())
        typ, _ = conn.append(selected, r"(\Seen)", now, raw)
        if typ != "OK":
            return False, f"APPEND {typ}"
        return True, None
    except Exception as exc:  # noqa: BLE001 — soft side-effect
        return False, f"{exc.__class__.__name__}: {exc}"
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                try:
                    conn.shutdown()
                except Exception:
                    pass


def send_via_smtp(
    *,
    email_addr: str,
    password: str,
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    imap_host: str | None = None,
    imap_port: int | None = None,
    save_to_sent: bool = True,
) -> SendResult:
    if not email_addr or not password:
        return SendResult(ok=False, error="缺少发件邮箱或密码")
    recipients = [a.strip() for a in to if a and a.strip()]
    if not recipients:
        return SendResult(ok=False, error="收件人不能为空")

    try:
        hint = resolve_smtp_host(email_addr, smtp_host=smtp_host, smtp_port=smtp_port)
    except ValueError as exc:
        return SendResult(ok=False, error=str(exc))

    msg = _build_outbound_message(
        email_addr=email_addr,
        recipients=recipients,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )

    try:
        from app.services.ssrf import resolve_mail_endpoint

        connect_host, connect_port, sni = resolve_mail_endpoint(
            hint.host, hint.port, use_ssl=hint.use_ssl or hint.use_starttls
        )
        context = ssl.create_default_context()
        # TCP may use pinned IP; set _host so STARTTLS/hostname checks use real name.
        tls_host = sni or hint.host
        if hint.use_ssl:
            with smtplib.SMTP_SSL(
                connect_host,
                connect_port,
                timeout=30,
                context=context,
            ) as smtp:
                try:
                    smtp._host = tls_host  # type: ignore[attr-defined]
                except Exception:
                    pass
                smtp.login(email_addr, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(connect_host, connect_port, timeout=30) as smtp:
                try:
                    smtp._host = tls_host  # type: ignore[attr-defined]
                except Exception:
                    pass
                smtp.ehlo()
                if hint.use_starttls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(email_addr, password)
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return SendResult(ok=False, error="SMTP 认证失败，请检查授权码/应用专用密码")
    except smtplib.SMTPException as exc:
        return SendResult(ok=False, error=f"SMTP 发信失败: {exc}")
    except OSError as exc:
        return SendResult(ok=False, error=f"SMTP 连接失败: {exc}")
    except Exception as exc:
        from app.services.ssrf import SsrfError

        if isinstance(exc, SsrfError):
            return SendResult(ok=False, error=str(exc.message if hasattr(exc, "message") else exc))
        return SendResult(ok=False, error=f"SMTP 发信失败: {exc}")

    detail = f"sent via smtp {hint.host}"
    saved = False
    if save_to_sent:
        ok_append, append_err = append_to_sent_imap(
            email_addr=email_addr,
            password=password,
            raw_message=msg,
            imap_host=imap_host,
            imap_port=imap_port,
        )
        saved = ok_append
        if ok_append:
            detail = f"{detail}; saved to Sent"
        elif append_err:
            # Soft failure — mail was still sent
            detail = f"{detail}; Sent APPEND skipped ({append_err})"

    return SendResult(ok=True, detail=detail, saved_to_sent=saved)


def send_mail(
    *,
    email: str,
    provider: str,
    password: str | None = None,
    credential: dict[str, Any] | None = None,
    to: list[str],
    subject: str,
    body_text: str = "",
    body_html: str | None = None,
    proxy: str | None = None,
) -> SendResult:
    """Dispatch send by provider. Cookie/http_api not supported for send."""
    creds = dict(credential or {})
    prov = (provider or "").lower()

    if prov == "oauth":
        result = send_via_graph(
            client_id=str(creds.get("client_id") or ""),
            refresh_token=str(creds.get("refresh_token") or ""),
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            proxy=proxy or (str(creds.get("proxy")) if creds.get("proxy") else None),
        )
        if result.ok:
            result.saved_to_sent = True  # Graph saveToSentItems
        return result

    if prov in ("imap", "unknown", "cookie"):
        # SMTP only — never pass imap_host as SMTP (Gmail imap.gmail.com breaks send)
        raw_pw = password or str(creds.get("password") or creds.get("auth_code") or "")
        pw = normalize_imap_secret(raw_pw, email)
        smtp_host = creds.get("smtp_host") or creds.get("smtpHost")
        smtp_port = creds.get("smtp_port") or creds.get("smtpPort")
        imap_host = creds.get("imap_host") or creds.get("imapHost") or creds.get("host")
        imap_port = creds.get("imap_port") or creds.get("imapPort") or creds.get("port")
        # Optional: derive from imap host via resolver normalizer, not raw imap host
        if not smtp_host and imap_host:
            smtp_host = str(imap_host)
        # Cookie/mail.com rarely has IMAP — still try domain SMTP; APPEND may soft-fail
        return send_via_smtp(
            email_addr=email,
            password=pw,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            smtp_host=str(smtp_host) if smtp_host else None,
            smtp_port=int(smtp_port) if smtp_port is not None else None,
            imap_host=str(imap_host) if imap_host else None,
            imap_port=int(imap_port) if imap_port is not None else None,
            save_to_sent=prov != "cookie",  # no public IMAP for typical mail.com free
        )

    if prov == "http_api":
        return SendResult(ok=False, error="HttpApi 账号不支持发信")

    return SendResult(ok=False, error=f"不支持的发信类型: {provider}")
