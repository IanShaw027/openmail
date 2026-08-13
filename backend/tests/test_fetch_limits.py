"""since and before must both reach providers during catch-up paging."""

from __future__ import annotations

from app.services.fetch_service import build_fetch_limits


def test_build_fetch_limits_keeps_since_when_before_is_set() -> None:
    limits = build_fetch_limits(
        since="2026-08-01T00:00:00+00:00",
        before="2026-08-12T12:00:00+00:00",
        full=False,
        quick=False,
        max_messages=50,
    )
    assert limits["since"] == "2026-08-01T00:00:00+00:00"
    assert limits["before"] == "2026-08-12T12:00:00+00:00"
    assert limits["max_messages"] == 50


def test_build_fetch_limits_omits_since_on_full_sync() -> None:
    limits = build_fetch_limits(
        since="2026-08-01T00:00:00+00:00",
        before=None,
        full=True,
        quick=False,
        max_messages=None,
    )
    assert "since" not in limits
