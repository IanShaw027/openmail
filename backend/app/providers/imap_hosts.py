"""Default IMAP host table by email domain.

Used when credentials omit imap_host. Port defaults to 993 (SSL).
Microsoft consumer domains default to outlook.office365.com (IMAP);
OAuth Graph is still preferred when client_id + refresh_token are present.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImapHostHint:
    host: str
    port: int = 993
    ssl: bool = True


# Domain → IMAP endpoint (lowercase keys; match email domain suffix)
DOMAIN_IMAP_HOSTS: dict[str, ImapHostHint] = {
    "qq.com": ImapHostHint("imap.qq.com"),
    "foxmail.com": ImapHostHint("imap.qq.com"),
    "163.com": ImapHostHint("imap.163.com"),
    "126.com": ImapHostHint("imap.126.com"),
    "yeah.net": ImapHostHint("imap.yeah.net"),
    "gmail.com": ImapHostHint("imap.gmail.com"),
    "googlemail.com": ImapHostHint("imap.gmail.com"),
    # Microsoft — IMAP available; basic auth often disabled → prefer OAuth Graph in router
    "outlook.com": ImapHostHint("outlook.office365.com"),
    "hotmail.com": ImapHostHint("outlook.office365.com"),
    "live.com": ImapHostHint("outlook.office365.com"),
    "msn.com": ImapHostHint("outlook.office365.com"),
    "office365.com": ImapHostHint("outlook.office365.com"),
    "icloud.com": ImapHostHint("imap.mail.me.com"),
    "me.com": ImapHostHint("imap.mail.me.com"),
    "mac.com": ImapHostHint("imap.mail.me.com"),
    "yahoo.com": ImapHostHint("imap.mail.yahoo.com"),
    "ymail.com": ImapHostHint("imap.mail.yahoo.com"),
    "aliyun.com": ImapHostHint("imap.aliyun.com"),
    "mxhichina.com": ImapHostHint("imap.mxhichina.com"),
    "gmx.com": ImapHostHint("imap.gmx.com"),
    "gmx.net": ImapHostHint("imap.gmx.net"),
    "gmx.de": ImapHostHint("imap.gmx.net"),
    "zoho.com": ImapHostHint("imap.zoho.com"),
    "zohomail.com": ImapHostHint("imap.zoho.com"),
}


def email_domain(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip()


def _safe_host(host: str, port: int) -> str:
    from app.services.ssrf import SsrfError, validate_mail_host

    try:
        return validate_mail_host(host, port=port, resolve_dns=True)
    except SsrfError as e:
        raise ValueError(str(e.message if hasattr(e, "message") else e)) from e


def resolve_imap_host(
    email: str,
    *,
    imap_host: str | None = None,
    imap_port: int | None = None,
    imap_ssl: bool | None = None,
) -> ImapHostHint:
    """Resolve host/port/ssl from explicit credentials or domain table.

    Custom hosts are SSRF-checked (no private/metadata IPs).
    """
    if imap_host:
        port = int(imap_port) if imap_port is not None else 993
        ssl = True if imap_ssl is None else bool(imap_ssl)
        host = _safe_host(imap_host.strip(), port)
        return ImapHostHint(host=host, port=port, ssl=ssl)

    domain = email_domain(email)
    if domain in DOMAIN_IMAP_HOSTS:
        hint = DOMAIN_IMAP_HOSTS[domain]
        port = int(imap_port) if imap_port is not None else hint.port
        return ImapHostHint(
            host=hint.host,
            port=port,
            ssl=hint.ssl if imap_ssl is None else bool(imap_ssl),
        )

    for key, hint in DOMAIN_IMAP_HOSTS.items():
        if domain.endswith("." + key):
            port = int(imap_port) if imap_port is not None else hint.port
            return ImapHostHint(
                host=hint.host,
                port=port,
                ssl=hint.ssl if imap_ssl is None else bool(imap_ssl),
            )

    # Aliyun enterprise mail domains often use mxhichina/qiye
    if "qiye.aliyun" in domain or domain.endswith(".aliyun.com"):
        port = int(imap_port) if imap_port is not None else 993
        return ImapHostHint(
            host="imap.qiye.aliyun.com",
            port=port,
            ssl=True if imap_ssl is None else bool(imap_ssl),
        )
    if domain.endswith(".mxhichina.com"):
        port = int(imap_port) if imap_port is not None else 993
        return ImapHostHint(
            host="imap.mxhichina.com",
            port=port,
            ssl=True if imap_ssl is None else bool(imap_ssl),
        )

    raise ValueError(f"无法推断 IMAP 主机: {domain or email}")
