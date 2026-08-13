"""SSRF protection for outbound HttpApi (and similar) fetches.

Rules:
- Only http / https schemes
- Resolve DNS and block private, loopback, link-local, multicast, reserved, metadata
- Re-validate every redirect hop (no open redirect into private nets)
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlparse, urljoin, urlunparse

# AWS / cloud metadata hostnames often used in SSRF
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)


class SsrfError(ValueError):
    """URL rejected by SSRF policy. Message is safe to show users."""

    def __init__(self, message: str = "URL blocked by SSRF policy / URL 被 SSRF 策略拦截") -> None:
        self.message = message
        super().__init__(message)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    # Non-global covers private, loopback, link-local, multicast, reserved,
    # unspecified, and CGNAT (100.64.0.0/10) including Aliyun IMDS 100.100.100.200.
    if not ip.is_global:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return True
    if isinstance(ip, ipaddress.IPv6Address) and (ip.packed[0] & 0xFE) == 0xFC:
        return True
    return False


def _resolve_host(hostname: str) -> list[str]:
    """Resolve hostname to IP strings. Empty list if resolution fails."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfError(f"DNS resolution failed / DNS 解析失败: {hostname}") from exc
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            ips.append(addr)
    return ips


def _check_hostname_literal(hostname: str) -> None:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        raise SsrfError("URL host missing / URL 缺少主机名")
    if host in _BLOCKED_HOSTNAMES:
        raise SsrfError("Blocked metadata hostname / 禁止访问元数据主机")
    # Literal IP in host
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if _is_blocked_ip(ip):
        raise SsrfError(f"Blocked IP address / 禁止访问的 IP: {host}")


def validate_url(
    url: str,
    *,
    resolve_dns: bool = True,
    pin_ip: bool = False,
) -> str:
    """Validate URL for outbound fetch.

    When pin_ip=True and host is a hostname (not literal IP), rewrite the URL
    to use a resolved public IP and put the original host in Host header via
    returned pin metadata — use validate_url_pinned() for that.
    """
    if not url or not isinstance(url, str):
        raise SsrfError("URL required / 需要 URL")

    raw = url.strip()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SsrfError("Only http/https allowed / 仅允许 http/https")

    if not parsed.hostname:
        raise SsrfError("URL host missing / URL 缺少主机名")

    # Reject credentials in URL (often used in SSRF tricks / secret leaks)
    if parsed.username or parsed.password:
        raise SsrfError("URL userinfo not allowed / URL 不允许嵌入用户名密码")

    # Port sanity
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise SsrfError("Invalid port / 端口无效")

    _check_hostname_literal(parsed.hostname)

    if resolve_dns:
        # Skip DNS for pure IP hosts already checked
        try:
            ipaddress.ip_address(parsed.hostname)
            literal = True
        except ValueError:
            literal = False
        if not literal:
            ips = _resolve_host(parsed.hostname)
            if not ips:
                raise SsrfError(f"DNS resolution failed / DNS 解析失败: {parsed.hostname}")
            for ip_str in ips:
                try:
                    ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    continue
                if _is_blocked_ip(ip):
                    raise SsrfError(
                        f"URL resolves to blocked address / URL 解析到禁止地址: {ip_str}"
                    )

    return raw


def pick_safe_ip(hostname: str) -> str:
    """Resolve hostname and return first non-blocked IP, or raise SsrfError."""
    try:
        ipaddress.ip_address(hostname)
        # already literal — re-check
        _check_hostname_literal(hostname)
        return hostname
    except ValueError:
        pass
    ips = _resolve_host(hostname)
    if not ips:
        raise SsrfError(f"DNS resolution failed / DNS 解析失败: {hostname}")
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not _is_blocked_ip(ip):
            return ip_str
    raise SsrfError(f"URL resolves only to blocked addresses / 仅解析到禁止地址: {hostname}")


def pin_url_to_ip(url: str) -> tuple[str, str, dict[str, str]]:
    """Validate URL, resolve once, return (connect_url, original_host, extra_headers).

    connect_url uses the pinned IP as netloc so httpx does not re-resolve the
    hostname (mitigates DNS rebinding between check and connect).
    """
    raw = validate_url(url, resolve_dns=True)
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = parsed.port
    try:
        ipaddress.ip_address(host)
        # literal IP already validated
        return raw, host, {}
    except ValueError:
        pass

    ip = pick_safe_ip(host)
    # Bracket IPv6
    try:
        ip_obj = ipaddress.ip_address(ip)
        netloc_host = f"[{ip}]" if isinstance(ip_obj, ipaddress.IPv6Address) else ip
    except ValueError:
        netloc_host = ip
    if port:
        netloc = f"{netloc_host}:{port}"
    else:
        netloc = netloc_host
    pinned = parsed._replace(netloc=netloc).geturl()
    headers = {"Host": host if not port else f"{host}:{port}"}
    # Default port: Host header should be bare hostname
    if port in (None, 80, 443):
        headers = {"Host": host}
    return pinned, host, headers


# Schemes httpx understands for the `proxy=` argument.
_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


def validate_proxy_url(proxy: str, *, allow_private: bool | None = None) -> str:
    """Validate an outbound proxy URL against the same policy as a target URL.

    The SSRF checks only ever looked at the destination, but the proxy is just
    as much an outbound connection: pointing it at ``http://127.0.0.1:9200``
    makes the server open a socket to a loopback service and send it a request
    line. Many internal HTTP services answer that, and even when they do not,
    the difference between refused, accepted and timed out is a working port
    scan of the host network.

    Self-hosted deployments legitimately proxy through a sidecar on a private
    address (the bundled WARP pool does exactly that), so private targets are
    allowed when configured — but only from server-side configuration, never
    from a client-supplied credential.
    """
    raw = (proxy or "").strip()
    if not raw:
        raise SsrfError("Proxy URL required / 需要代理地址")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _PROXY_SCHEMES:
        raise SsrfError(f"Unsupported proxy scheme / 不支持的代理协议: {scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise SsrfError("Proxy host missing / 代理缺少主机名")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise SsrfError("Invalid proxy port / 代理端口无效")

    if allow_private is None:
        from app.config import get_settings

        allow_private = bool(getattr(get_settings(), "allow_private_proxy", False))
    if allow_private:
        return raw

    _check_hostname_literal(host)
    try:
        ipaddress.ip_address(host)
        return raw
    except ValueError:
        ip = pick_safe_ip(host)
        return _proxy_url_with_pinned_host(parsed, ip)


def _proxy_url_with_pinned_host(parsed, ip: str) -> str:  # type: ignore[no-untyped-def]
    """Replace hostname with a already-checked IP; keep userinfo, port, path."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        host = f"[{ip}]" if isinstance(ip_obj, ipaddress.IPv6Address) else ip
    except ValueError:
        host = ip
    port = parsed.port
    hostport = f"{host}:{port}" if port is not None else host
    netloc = parsed.netloc
    at = netloc.rfind("@")
    if at != -1:
        hostport = netloc[: at + 1] + hostport
    return urlunparse(
        (parsed.scheme, hostport, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def is_safe_url(url: str, *, resolve_dns: bool = True) -> bool:
    try:
        validate_url(url, resolve_dns=resolve_dns)
        return True
    except SsrfError:
        return False


def validate_redirect_target(base_url: str, location: str) -> str:
    """Validate a redirect Location header relative to base_url."""
    if not location:
        raise SsrfError("Empty redirect / 空重定向")
    absolute = urljoin(base_url, location)
    return validate_url(absolute, resolve_dns=True)


def filter_safe_urls(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    for u in urls:
        try:
            out.append(validate_url(u))
        except SsrfError:
            continue
    return out


# Mail protocol hosts (IMAP/SMTP) — same private/metadata blocks, no URL scheme
_MAIL_ALLOWED_PORTS = frozenset({25, 465, 587, 993, 995, 143, 110})


def validate_mail_host(
    host: str,
    *,
    port: int | None = None,
    resolve_dns: bool = True,
    allow_any_port: bool = False,
    pin_ip: bool = False,
) -> str:
    """Validate IMAP/SMTP hostname against SSRF policy.

    Returns stripped host, or when pin_ip=True a safe resolved IP string
    (caller should connect to IP and use original host for TLS SNI/cert).
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        raise SsrfError("Mail host missing / 邮件主机缺失")
    if h in _BLOCKED_HOSTNAMES:
        raise SsrfError("Blocked metadata hostname / 禁止访问元数据主机")
    if "/" in h or "\\" in h or "@" in h or " " in h:
        raise SsrfError("Invalid mail host / 邮件主机无效")

    if port is not None:
        p = int(port)
        if not (1 <= p <= 65535):
            raise SsrfError("Invalid port / 端口无效")
        if not allow_any_port and p not in _MAIL_ALLOWED_PORTS:
            raise SsrfError(
                f"Mail port not allowed / 不允许的邮件端口: {p} "
                f"(allowed: {sorted(_MAIL_ALLOWED_PORTS)})"
            )

    # Literal IP
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_blocked_ip(ip):
            raise SsrfError(f"Blocked IP address / 禁止访问的 IP: {h}")
        return h

    if resolve_dns:
        safe_ip = pick_safe_ip(h)
        if pin_ip:
            return safe_ip
        return h
    return h


def resolve_mail_endpoint(
    host: str,
    port: int,
    *,
    use_ssl: bool = True,
) -> tuple[str, int, str]:
    """Return (connect_host, port, tls_server_hostname).

    connect_host is a pinned public IP when host is a name; tls_server_hostname
    is the original hostname for SNI/cert verification.
    """
    h = (host or "").strip()
    try:
        ipaddress.ip_address(h.strip().lower().rstrip("."))
        safe = validate_mail_host(h, port=port, resolve_dns=False)
        return safe, port, safe
    except ValueError:
        pass
    safe_ip = validate_mail_host(h, port=port, resolve_dns=True, pin_ip=True)
    return safe_ip, port, h.strip().lower().rstrip(".")
