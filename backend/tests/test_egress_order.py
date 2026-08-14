"""Interactive direct-first egress vs bulk WARP-first."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.fetch_service import (
    _cap_egress_candidates,
    _is_direct_failover_error,
    _is_retryable_egress_error,
    resolve_egress_candidates,
)
from app.services.proxy import list_proxy_candidates


def _pool_settings() -> SimpleNamespace:
    return SimpleNamespace(
        proxy_template="",
        proxy_pool="socks5://warp-1:1080|socks5://warp-2:1080|socks5://warp-3:1080",
        proxy_sid_strategy="sticky_per_account",
    )


def test_cap_preserves_direct_first_order() -> None:
    assert _cap_egress_candidates(
        [None, "socks5://warp-1:1080", "socks5://warp-2:1080", "socks5://warp-3:1080"],
        max_attempts=3,
    ) == [None, "socks5://warp-1:1080", "socks5://warp-2:1080"]


def test_cap_keeps_direct_last_for_warp_first() -> None:
    assert _cap_egress_candidates(
        ["socks5://warp-1:1080", "socks5://warp-2:1080", "socks5://warp-3:1080", None],
        max_attempts=3,
    ) == ["socks5://warp-1:1080", "socks5://warp-2:1080", None]


def test_list_proxy_candidates_prefer_direct_puts_none_first() -> None:
    acc = SimpleNamespace(id=None, proxy=None, email="user@mail.com")
    cands = list_proxy_candidates(
        acc, settings=_pool_settings(), include_direct=True, prefer_direct=True
    )
    assert cands[0] is None
    assert all(isinstance(x, str) and x.startswith("socks5://warp-") for x in cands[1:])


def test_interactive_imap_is_direct_then_warp() -> None:
    acc = SimpleNamespace(id=None, proxy=None, email="u@qq.com", provider="imap")
    ordered = resolve_egress_candidates(
        acc, settings=_pool_settings(), provider="imap", egress_mode="interactive"
    )
    assert ordered[0] is None
    assert len(ordered) == 3
    assert all(isinstance(x, str) for x in ordered[1:])


def test_bulk_imap_stays_warp_first() -> None:
    acc = SimpleNamespace(id=None, proxy=None, email="u@qq.com", provider="imap")
    ordered = resolve_egress_candidates(
        acc, settings=_pool_settings(), provider="imap", egress_mode="bulk"
    )
    assert ordered[0] is not None
    assert ordered[-1] is None
    assert len(ordered) == 3


def test_cookie_interactive_stays_warp_first() -> None:
    acc = SimpleNamespace(id=None, proxy=None, email="u@mail.com", provider="cookie")
    ordered = resolve_egress_candidates(
        acc, settings=_pool_settings(), provider="cookie", egress_mode="interactive"
    )
    assert ordered[0] is not None
    assert None in ordered
    assert ordered[0] != None  # noqa: E711 — first hop is WARP


def test_direct_failover_is_narrow() -> None:
    assert _is_direct_failover_error("IMAP 连接超时或网络错误: timed out")
    assert _is_direct_failover_error("connection refused")
    assert _is_direct_failover_error("Network is unreachable")
    assert _is_direct_failover_error("421 Service not available")
    assert not _is_direct_failover_error("IMAP 认证失败，请检查授权码")
    assert not _is_direct_failover_error("刷新令牌无效或已过期 / Refresh token invalid")
    assert not _is_direct_failover_error("Graph 请求失败 (429) / Graph request failed (429)")
    assert not _is_direct_failover_error("登录失败 / login failed")
    # Broad retry helper still matches mailbox-down text; failover must not.
    assert _is_retryable_egress_error("请求失败")
    assert not _is_direct_failover_error("请求失败")
    assert _is_direct_failover_error("IMAP 错误: socket error: EOF")


def test_interactive_http_api_stays_warp_first() -> None:
    acc = SimpleNamespace(id=None, proxy=None, email="api@host", provider="http_api")
    ordered = resolve_egress_candidates(
        acc, settings=_pool_settings(), provider="http_api", egress_mode="interactive"
    )
    assert ordered[0] is not None
    assert ordered[-1] is None

