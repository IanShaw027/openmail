"""Public code-API token rate limiting (abuse control for leaked token URLs)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.license import check_code_api_quota


def _settings(fetch_limit: int, refresh_limit: int) -> SimpleNamespace:
    return SimpleNamespace(
        code_api_max_fetch_per_hour=fetch_limit,
        code_api_max_refresh_per_hour=refresh_limit,
    )


def test_code_api_quota_blocks_after_limit(db_session):
    s = _settings(fetch_limit=3, refresh_limit=10)
    token = "tok_abc"

    for _ in range(3):
        allowed, err = check_code_api_quota(token, settings=s, db=db_session)
        assert allowed, err

    allowed, err = check_code_api_quota(token, settings=s, db=db_session)
    assert not allowed
    assert "rate limit" in (err or "")


def test_code_api_refresh_has_stricter_limit(db_session):
    # Generous base limit, but refresh is capped tighter.
    s = _settings(fetch_limit=100, refresh_limit=2)
    token = "tok_refresh"

    for _ in range(2):
        allowed, err = check_code_api_quota(token, refresh=True, settings=s, db=db_session)
        assert allowed, err

    allowed, _ = check_code_api_quota(token, refresh=True, settings=s, db=db_session)
    assert not allowed


def test_code_api_quota_is_per_token(db_session):
    s = _settings(fetch_limit=1, refresh_limit=10)

    assert check_code_api_quota("token_a", settings=s, db=db_session)[0]
    # Different token has its own bucket.
    assert check_code_api_quota("token_b", settings=s, db=db_session)[0]
    # token_a is now exhausted.
    assert not check_code_api_quota("token_a", settings=s, db=db_session)[0]
