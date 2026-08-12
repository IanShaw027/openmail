"""Public code-API token rate limiting (abuse control for leaked token URLs)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.license import check_code_api_miss_quota, check_code_api_quota


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


def test_zero_disables_the_limit_instead_of_falling_back_to_the_default(db_session):
    # A falsy 0 used to be read as "unset" and silently replaced by 60/hour.
    s = _settings(fetch_limit=0, refresh_limit=0)

    for _ in range(70):
        assert check_code_api_quota("tok_unlimited", settings=s, db=db_session)[0]
    for _ in range(20):
        assert check_code_api_quota("tok_unlimited", refresh=True, settings=s, db=db_session)[0]


def test_zero_refresh_limit_still_charges_the_base_limit(db_session):
    s = _settings(fetch_limit=2, refresh_limit=0)

    assert check_code_api_quota("tok_mixed", refresh=True, settings=s, db=db_session)[0]
    assert check_code_api_quota("tok_mixed", refresh=True, settings=s, db=db_session)[0]
    assert not check_code_api_quota("tok_mixed", refresh=True, settings=s, db=db_session)[0]


def test_unknown_token_requests_are_throttled_per_ip(db_session):
    s = _settings(fetch_limit=2, refresh_limit=10)

    assert check_code_api_miss_quota("203.0.113.7", settings=s, db=db_session)[0]
    assert check_code_api_miss_quota("203.0.113.7", settings=s, db=db_session)[0]
    allowed, err = check_code_api_miss_quota("203.0.113.7", settings=s, db=db_session)
    assert not allowed
    assert "rate limit" in (err or "")

    # A different client is unaffected.
    assert check_code_api_miss_quota("198.51.100.4", settings=s, db=db_session)[0]


def test_blocked_refresh_does_not_spend_the_base_budget(db_session):
    # Refresh is capped tighter, so a rejected refresh must not also burn one of
    # the plain fetches the caller is still entitled to.
    s = _settings(fetch_limit=10, refresh_limit=1)
    token = "tok_budget"

    assert check_code_api_quota(token, refresh=True, settings=s, db=db_session)[0]
    for _ in range(5):
        assert not check_code_api_quota(token, refresh=True, settings=s, db=db_session)[0]

    # One refresh was charged; the five rejected ones spent nothing.
    remaining = 0
    while check_code_api_quota(token, settings=s, db=db_session)[0]:
        remaining += 1
        if remaining > 20:
            break
    assert remaining == 9


def test_miss_quota_does_not_consume_the_per_token_budget(db_session):
    # Enumeration attempts must not be able to exhaust a real token's quota.
    s = _settings(fetch_limit=2, refresh_limit=10)

    assert check_code_api_miss_quota("203.0.113.9", settings=s, db=db_session)[0]
    assert check_code_api_miss_quota("203.0.113.9", settings=s, db=db_session)[0]

    assert check_code_api_quota("real_token", settings=s, db=db_session)[0]
    assert check_code_api_quota("real_token", settings=s, db=db_session)[0]
