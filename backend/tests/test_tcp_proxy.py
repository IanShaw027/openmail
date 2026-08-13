"""SOCKS5 / HTTP CONNECT helper used by IMAP and SMTP egress."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class _ScriptedSock:
    def __init__(self, replies: list[bytes]) -> None:
        self._buf = bytearray()
        for chunk in replies:
            self._buf.extend(chunk)
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def settimeout(self, _t: float | None) -> None:
        return None

    def close(self) -> None:
        return None


def test_socks5_connect_uses_pinned_ipv4_not_hostname() -> None:
    # greeting OK (no auth) + CONNECT succeeded, bind 0.0.0.0:0
    sock = _ScriptedSock(
        [
            b"\x05\x00",
            b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00",
        ]
    )
    from app.services.tcp_proxy import open_proxied_tcp

    with patch("socket.create_connection", return_value=sock) as cc:
        out = open_proxied_tcp(
            "socks5://127.0.0.1:1080",
            "93.184.216.34",
            993,
            timeout=5.0,
        )
    assert out is sock
    cc.assert_called_once()
    assert cc.call_args[0][0] == ("127.0.0.1", 1080)
    # CONNECT ATYP=IPv4 to 93.184.216.34:993 — not the original hostname
    assert b"imap.example.com" not in sock.sent
    assert bytes((93, 184, 216, 34)) in sock.sent
    assert (993).to_bytes(2, "big") in sock.sent


def test_http_connect_sends_connect_to_ip() -> None:
    from app.services.tcp_proxy import open_proxied_tcp

    sock = _ScriptedSock([b"HTTP/1.1 200 Connection established\r\n\r\n"])
    with patch("socket.create_connection", return_value=sock):
        out = open_proxied_tcp(
            "http://127.0.0.1:8080",
            "93.184.216.34",
            993,
            timeout=5.0,
        )
    assert out is sock
    first = sock.sent.split(b"\r\n", 1)[0]
    assert first == b"CONNECT 93.184.216.34:993 HTTP/1.1"


def test_socks5_connect_failure_raises() -> None:
    from app.services.tcp_proxy import open_proxied_tcp

    sock = _ScriptedSock(
        [
            b"\x05\x00",
            b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00",  # connection refused
        ]
    )
    with (
        patch("socket.create_connection", return_value=sock),
        pytest.raises(OSError),
    ):
        open_proxied_tcp("socks5://127.0.0.1:1080", "93.184.216.34", 993, timeout=5.0)
