"""SMTP host table for outbound mail (paired with IMAP domains)."""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.imap_hosts import email_domain


@dataclass(frozen=True)
class SmtpHostHint:
    host: str
    port: int = 587
    use_starttls: bool = True
    use_ssl: bool = False


DOMAIN_SMTP_HOSTS: dict[str, SmtpHostHint] = {
    "qq.com": SmtpHostHint("smtp.qq.com"),
    "foxmail.com": SmtpHostHint("smtp.qq.com"),
    "163.com": SmtpHostHint("smtp.163.com"),
    "126.com": SmtpHostHint("smtp.126.com"),
    "yeah.net": SmtpHostHint("smtp.yeah.net"),
    "gmail.com": SmtpHostHint("smtp.gmail.com"),
    "googlemail.com": SmtpHostHint("smtp.gmail.com"),
    "outlook.com": SmtpHostHint("smtp.office365.com"),
    "hotmail.com": SmtpHostHint("smtp.office365.com"),
    "live.com": SmtpHostHint("smtp.office365.com"),
    "msn.com": SmtpHostHint("smtp.office365.com"),
    "icloud.com": SmtpHostHint("smtp.mail.me.com"),
    "me.com": SmtpHostHint("smtp.mail.me.com"),
    "mac.com": SmtpHostHint("smtp.mail.me.com"),
    "yahoo.com": SmtpHostHint("smtp.mail.yahoo.com"),
    "ymail.com": SmtpHostHint("smtp.mail.yahoo.com"),
    "aliyun.com": SmtpHostHint("smtp.aliyun.com"),
    "mxhichina.com": SmtpHostHint("smtp.mxhichina.com"),
    # GMX (United Internet)
    "gmx.com": SmtpHostHint("mail.gmx.com"),
    "gmx.net": SmtpHostHint("mail.gmx.net"),
    "gmx.de": SmtpHostHint("mail.gmx.net"),
    # Zoho
    "zoho.com": SmtpHostHint("smtp.zoho.com"),
    "zohomail.com": SmtpHostHint("smtp.zoho.com"),
    # United Internet / mail.com family — free webmail usually has SMTP with
    # the same password as login (when not blocked); cookie send is preferred
    # in send_service for provider=cookie. Table kept for IMAP-class imports.
    "mail.com": SmtpHostHint("smtp.mail.com", port=587),
    "email.com": SmtpHostHint("smtp.mail.com", port=587),
    "usa.com": SmtpHostHint("smtp.mail.com", port=587),
    "myself.com": SmtpHostHint("smtp.mail.com", port=587),
    "consultant.com": SmtpHostHint("smtp.mail.com", port=587),
    "europe.com": SmtpHostHint("smtp.mail.com", port=587),
    "iname.com": SmtpHostHint("smtp.mail.com", port=587),
    "writeme.com": SmtpHostHint("smtp.mail.com", port=587),
    "techie.com": SmtpHostHint("smtp.mail.com", port=587),
    "dr.com": SmtpHostHint("smtp.mail.com", port=587),
    "engineer.com": SmtpHostHint("smtp.mail.com", port=587),
    "cheerful.com": SmtpHostHint("smtp.mail.com", port=587),
    # Proton Bridge is local-only (127.0.0.1:1025) and blocked by SSRF defaults —
    # users must pass explicit smtp_host if they open that path; no public cloud SMTP.
}


def _normalize_smtp_host(host: str) -> str:
    """Map common IMAP hostnames to SMTP (never use imap.* as SMTP)."""
    h = (host or "").strip().lower()
    if not h:
        return h
    # explicit known swaps
    swaps = {
        "imap.gmail.com": "smtp.gmail.com",
        "imap.googlemail.com": "smtp.gmail.com",
        "imap.qq.com": "smtp.qq.com",
        "imap.163.com": "smtp.163.com",
        "imap.126.com": "smtp.126.com",
        "imap.yeah.net": "smtp.yeah.net",
        "imap.mail.me.com": "smtp.mail.me.com",
        "imap.mail.yahoo.com": "smtp.mail.yahoo.com",
        "imap.qiye.aliyun.com": "smtp.qiye.aliyun.com",
        "imap.mxhichina.com": "smtp.mxhichina.com",
        "outlook.office365.com": "smtp.office365.com",
        "imap.gmx.com": "mail.gmx.com",
        "imap.gmx.net": "mail.gmx.net",
        "imap.zoho.com": "smtp.zoho.com",
        "imap.zoho.eu": "smtp.zoho.eu",
    }
    if h in swaps:
        return swaps[h]
    if h.startswith("imap."):
        return "smtp." + h[5:]
    return host.strip()


def resolve_smtp_host(
    email: str,
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
) -> SmtpHostHint:
    if smtp_host:
        from app.services.ssrf import SsrfError, validate_mail_host

        host = _normalize_smtp_host(str(smtp_host))
        port = int(smtp_port) if smtp_port is not None else 587
        try:
            host = validate_mail_host(host, port=port, resolve_dns=True)
        except SsrfError as e:
            raise ValueError(str(e.message if hasattr(e, "message") else e)) from e
        return SmtpHostHint(
            host=host,
            port=port,
            use_starttls=port != 465,
            use_ssl=port == 465,
        )

    domain = email_domain(email)
    if domain in DOMAIN_SMTP_HOSTS:
        hint = DOMAIN_SMTP_HOSTS[domain]
        if smtp_port is not None:
            port = int(smtp_port)
            return SmtpHostHint(
                host=hint.host,
                port=port,
                use_starttls=port != 465,
                use_ssl=port == 465,
            )
        return hint

    for key, hint in DOMAIN_SMTP_HOSTS.items():
        if domain.endswith("." + key):
            return hint

    if "qiye.aliyun" in domain or domain.endswith(".aliyun.com"):
        return SmtpHostHint(host="smtp.qiye.aliyun.com")
    if domain.endswith(".mxhichina.com"):
        return SmtpHostHint(host="smtp.mxhichina.com")

    raise ValueError(
        f"无法推断 SMTP 主机: {domain or email}。"
        f"非常规域名请在凭证中填写 smtp_host / smtp_port（如 smtp.example.com:465）"
    )
