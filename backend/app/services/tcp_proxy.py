"""TCP tunnel via SOCKS5 or HTTP CONNECT (IMAP/SMTP egress).

Destination must already be a pinned IP from ``resolve_mail_endpoint`` so the
proxy cannot re-resolve a hostname onto a private address (SSRF). The proxy
URL itself may be private (operator WARP pool).
"""

from __future__ import annotations

import base64
import ipaddress
import socket
from urllib.parse import unquote, urlparse

from socksio.socks5 import (
    SOCKS5AuthMethod,
    SOCKS5AuthMethodsRequest,
    SOCKS5Command,
    SOCKS5CommandRequest,
    SOCKS5Connection,
    SOCKS5Reply,
    SOCKS5ReplyCode,
    SOCKS5UsernamePasswordRequest,
)


def open_proxied_tcp(
    proxy_url: str,
    dest_host: str,
    dest_port: int,
    timeout: float = 30.0,
) -> socket.socket:
    """Return a connected socket tunneled through *proxy_url* to dest_host:dest_port."""
    parsed = urlparse(proxy_url.strip())
    scheme = (parsed.scheme or "").lower()
    proxy_host = parsed.hostname
    if not proxy_host:
        raise OSError("proxy host missing / 代理缺少主机名")
    proxy_port = parsed.port
    if proxy_port is None:
        proxy_port = 443 if scheme == "https" else 1080 if scheme.startswith("socks") else 8080
    user = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else ""

    sock = socket.create_connection((proxy_host, int(proxy_port)), timeout=timeout)
    try:
        sock.settimeout(timeout)
        if scheme in ("socks5", "socks5h"):
            _socks5_connect(sock, dest_host, dest_port, user=user, password=password)
        elif scheme in ("http", "https"):
            if scheme == "https":
                import ssl as _ssl

                sock = _ssl.create_default_context().wrap_socket(
                    sock, server_hostname=proxy_host
                )
            _http_connect(sock, dest_host, dest_port, user=user, password=password)
        else:
            raise OSError(f"unsupported proxy scheme / 不支持的代理协议: {scheme or '(none)'}")
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise
    return sock


def _recv_n(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("proxy closed during handshake / 代理握手中断开")
        buf.extend(chunk)
    return bytes(buf)


def _socks5_connect(
    sock: socket.socket,
    dest_host: str,
    dest_port: int,
    *,
    user: str | None,
    password: str,
) -> None:
    conn = SOCKS5Connection()
    methods = [SOCKS5AuthMethod.NO_AUTH_REQUIRED]
    if user is not None:
        methods = [SOCKS5AuthMethod.USERNAME_PASSWORD]
    conn.send(SOCKS5AuthMethodsRequest(methods))
    sock.sendall(conn.data_to_send())
    auth_reply = conn.receive_data(_recv_n(sock, 2))
    if auth_reply.method == SOCKS5AuthMethod.NO_ACCEPTABLE_METHODS:
        raise OSError("SOCKS5 proxy rejected auth methods / 代理拒绝认证方式")
    if auth_reply.method == SOCKS5AuthMethod.USERNAME_PASSWORD:
        if user is None:
            raise OSError("SOCKS5 proxy requires username / 代理需要用户名")
        conn.send(
            SOCKS5UsernamePasswordRequest(
                username=user.encode("utf-8"),
                password=password.encode("utf-8"),
            )
        )
        sock.sendall(conn.data_to_send())
        up = conn.receive_data(_recv_n(sock, 2))
        if not getattr(up, "success", False):
            raise OSError("SOCKS5 proxy auth failed / 代理用户名密码错误")

    # Always CONNECT to the caller-supplied address (pinned IP, not a hostname).
    conn.send(
        SOCKS5CommandRequest.from_address(
            SOCKS5Command.CONNECT, (dest_host, int(dest_port))
        )
    )
    sock.sendall(conn.data_to_send())
    header = _recv_n(sock, 4)
    atyp = header[3:4]
    if atyp == b"\x01":
        rest = _recv_n(sock, 6)
    elif atyp == b"\x04":
        rest = _recv_n(sock, 18)
    elif atyp == b"\x03":
        ln = _recv_n(sock, 1)
        rest = ln + _recv_n(sock, ln[0] + 2)
    else:
        raise OSError("SOCKS5 unknown address type / 代理返回未知地址类型")
    reply = conn.receive_data(header + rest)
    if not isinstance(reply, SOCKS5Reply) or reply.reply_code != SOCKS5ReplyCode.SUCCEEDED:
        code = getattr(reply, "reply_code", None)
        raise OSError(f"SOCKS5 CONNECT failed / 代理 CONNECT 失败: {code}")


def _http_connect(
    sock: socket.socket,
    dest_host: str,
    dest_port: int,
    *,
    user: str | None,
    password: str,
) -> None:
    host_port = _http_connect_target(dest_host, dest_port)
    lines = [
        f"CONNECT {host_port} HTTP/1.1",
        f"Host: {host_port}",
    ]
    if user is not None:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("HTTP proxy closed during CONNECT / 代理 CONNECT 中断开")
        buf.extend(chunk)
        if len(buf) > 8192:
            raise OSError("HTTP proxy CONNECT response too large / 代理响应过长")
    head = bytes(buf).split(b"\r\n", 1)[0]
    try:
        status = int(head.split()[1])
    except (IndexError, ValueError) as exc:
        raise OSError("HTTP proxy CONNECT malformed / 代理 CONNECT 响应无效") from exc
    if status != 200:
        raise OSError(f"HTTP proxy CONNECT failed / 代理 CONNECT 失败: {head!r}")


def _http_connect_target(dest_host: str, dest_port: int) -> str:
    host = dest_host.strip()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{int(dest_port)}"
    if isinstance(ip, ipaddress.IPv6Address):
        return f"[{ip}]:{int(dest_port)}"
    return f"{ip}:{int(dest_port)}"
