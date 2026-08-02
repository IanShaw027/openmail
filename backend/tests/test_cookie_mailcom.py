"""mail.com cookie provider unit tests with HTML fixtures (no live network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.providers.base import resolve_provider
from app.providers.cookie_mailcom import (
    MailcomCookieProvider,
    extract_ott,
    html_indicates_bad_credentials,
    is_transient_login_error,
    parse_forms,
    parse_message_detail_html,
    parse_message_list_html,
    pick_login_form,
    session_looks_valid,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mailcom"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_session_valid_marker() -> None:
    html = _load("folder_list_ok.html")
    assert session_looks_valid(html) is True
    assert session_looks_valid(_load("session_expired.html")) is False
    assert session_looks_valid(_load("login_page.html")) is False


def test_bad_credentials_not_false_positive_on_marketing() -> None:
    # marketing / cookie banners often contain the word "wrong" alone
    assert html_indicates_bad_credentials("Something went wrong with cookies") is False
    assert html_indicates_bad_credentials("Access denied to resource") is False
    assert html_indicates_bad_credentials("Invalid password. Please try again.") is True
    assert html_indicates_bad_credentials("密码错误，请重试") is True


def test_transient_login_error() -> None:
    assert is_transient_login_error("mail.com login parse failed") is True
    assert is_transient_login_error("登录页请求失败: timeout") is True
    # May be false positive under flaky SSO — outer loop retries
    assert is_transient_login_error("账号或密码错误") is True
    assert is_transient_login_error("mail.com 访问过于频繁或需要验证码，请稍后重试") is True


def test_extract_ott_strips_url_fragment() -> None:
    url = (
        "https://navigator-lxa.mail.com/login?edition=us"
        "&ott=3c152019-53b9-4da1-9c74-789bb9205941#.7518-header-login1-1"
    )
    assert extract_ott(url, "") == "3c152019-53b9-4da1-9c74-789bb9205941"
    html = 'noscript refresh ott=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee&hint=nojs'
    assert extract_ott("https://example.com/", html) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_parse_login_form() -> None:
    forms = parse_forms(_load("login_page.html"))
    form = pick_login_form(forms)
    assert form is not None
    assert form["action"] == "/login/submit"
    assert "username" in form["inputs"]
    assert "password" in form["inputs"]
    assert form["inputs"]["token"]["value"] == "csrf-abc-123"


def test_parse_message_list_fixture() -> None:
    msgs = parse_message_list_html(_load("folder_list_ok.html"), limit=10)
    assert len(msgs) == 2
    assert msgs[0].id == "msg-001"
    assert "verification" in msgs[0].subject.lower()
    assert msgs[0].from_address == "noreply@example.com"


def test_parse_message_detail_and_code() -> None:
    msg = parse_message_detail_html(_load("message_detail.html"), msg_id="msg-001")
    assert msg.subject == "Your verification code"
    assert msg.verification_code == "482913"
    assert "482913" in msg.body_text


def test_resolve_provider_cookie() -> None:
    acc = SimpleNamespace(provider="cookie", email="u@mail.com")
    p = resolve_provider(acc)
    assert p is not None
    assert p.name == "cookie"
    assert isinstance(p, MailcomCookieProvider)


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://www.mail.com/mail"):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.content = text.encode("utf-8")


class _FakeClient:
    """Minimal client stub for try_restore / full_login / fetch_message_list."""

    def __init__(self, routes: dict[str, str | _FakeResp]) -> None:
        self.routes = routes
        self.cookies = _FakeCookies()
        self.closed = False
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResp:
        for key, val in self.routes.items():
            if key in url or url.endswith(key) or url == key:
                if isinstance(val, _FakeResp):
                    return val
                return _FakeResp(val, url=url)
        # default: expired
        return _FakeResp(_load("session_expired.html"), url=url)

    def post(self, url: str, **kwargs: Any) -> _FakeResp:
        self.posts.append({"url": url, "data": kwargs.get("data")})
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, _FakeResp):
                    return val
                return _FakeResp(val, url=url)
        return _FakeResp(_load("folder_list_ok.html"), url=url)

    def close(self) -> None:
        self.closed = True


class _FakeCookies:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.jar: list[Any] = []

    def set(self, name: str, value: str, **kwargs: Any) -> None:
        self._data[name] = value
        c = SimpleNamespace(
            name=name,
            value=value,
            domain=kwargs.get("domain", ""),
            path=kwargs.get("path", "/"),
            secure=False,
            rest={},
        )
        self.jar = [x for x in self.jar if x.name != name] + [c]

    def clear(self) -> None:
        self._data.clear()
        self.jar.clear()

    def items(self):
        return self._data.items()


def test_try_restore_success() -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient({"/mail": _load("folder_list_ok.html")})
    ok, meta = provider.try_restore(
        client,
        [{"name": "sid", "value": "abc", "domain": ".mail.com", "path": "/"}],
        site="mail.com",
    )
    assert ok is True
    assert meta is not None
    assert meta.get("last_probe") == "restore_ok"


def test_try_restore_failure() -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient({"/mail": _load("session_expired.html")})
    ok, meta = provider.try_restore(
        client,
        [{"name": "sid", "value": "stale"}],
        site="mail.com",
    )
    assert ok is False
    assert meta is None


def test_full_login_posts_form() -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient(
        {
            "/login": _load("login_page.html"),
            "/login/submit": _load("folder_list_ok.html"),
            "/mail": _load("folder_list_ok.html"),
        }
    )
    ok, err, meta = provider.full_login(client, "user@mail.com", "secret", site="mail.com")
    assert ok is True, err
    assert err is None
    assert client.posts, "expected form POST"
    posted = client.posts[0]["data"]
    assert posted.get("username") == "user@mail.com"
    assert posted.get("password") == "secret"
    assert posted.get("token") == "csrf-abc-123"


def test_full_login_parse_failed() -> None:
    provider = MailcomCookieProvider()
    # Page with no password form and no captcha markers
    client = _FakeClient({"/login": "<html><body>empty portal</body></html>"})
    ok, err, _ = provider.full_login(client, "user@mail.com", "secret", site="mail.com")
    assert ok is False
    assert err == "mail.com login parse failed"


def test_full_login_captcha_wall() -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient({"/login": "<html><body>captcha wall</body></html>"})
    ok, err, _ = provider.full_login(client, "user@mail.com", "secret", site="mail.com")
    assert ok is False
    assert err is not None
    assert "验证码" in err or "频繁" in err


def test_fetch_message_list() -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient({"/mail": _load("folder_list_ok.html")})
    msgs = provider.fetch_message_list(client, limit=10, site="mail.com")
    assert len(msgs) == 2


def test_fetch_restores_cookies_and_returns_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MailcomCookieProvider()
    client = _FakeClient(
        {
            "/mail": _load("folder_list_ok.html"),
            "msg-001": _load("message_detail.html"),
        }
    )
    # seed cookie dump after restore
    client.cookies.set("sid", "rolling-1", domain=".mail.com")

    monkeypatch.setattr(
        "app.providers.cookie_mailcom._http_client",
        lambda *a, **k: client,
    )

    acc = SimpleNamespace(provider="cookie", email="user@mail.com", proxy=None)
    result = provider.fetch(
        acc,
        credentials={
            "password": "x",
            "cookies": [{"name": "sid", "value": "old"}],
            "site": "mail.com",
        },
    )
    assert result.ok is True
    assert result.session_restored is True
    assert result.credential_updates is not None
    assert result.credential_updates.session_cookies is not None
    assert any(c.get("name") == "sid" for c in result.credential_updates.session_cookies)
    assert result.message_count == 2


def test_fetch_login_when_cookies_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MailcomCookieProvider()
    # First GETs return expired; login path returns form then OK folder
    state = {"n": 0}

    class _SeqClient(_FakeClient):
        def get(self, url: str, **kwargs: Any) -> _FakeResp:
            if "/login" in url and "submit" not in url:
                return _FakeResp(_load("login_page.html"), url=url)
            if "mail" in url or url.endswith("/"):
                # before login, expired; after post, ok
                if self.posts:
                    return _FakeResp(_load("folder_list_ok.html"), url=url)
                return _FakeResp(_load("session_expired.html"), url=url)
            return super().get(url, **kwargs)

        def post(self, url: str, **kwargs: Any) -> _FakeResp:
            self.posts.append({"url": url, "data": kwargs.get("data")})
            return _FakeResp(_load("folder_list_ok.html"), url=url)

    client = _SeqClient({})
    monkeypatch.setattr(
        "app.providers.cookie_mailcom._http_client",
        lambda *a, **k: client,
    )
    acc = SimpleNamespace(provider="cookie", email="user@mail.com", proxy=None)
    result = provider.fetch(
        acc,
        credentials={
            "password": "secret",
            "cookies": [{"name": "sid", "value": "dead"}],
            "site": "mail.com",
        },
    )
    assert result.ok is True
    assert result.session_restored is False
    assert client.posts


@pytest.mark.network
def test_live_mailcom_skipped_without_env() -> None:
    """Live network test placeholder — skipped unless OPENMAIL_LIVE_MAILCOM=1."""
    import os

    if os.environ.get("OPENMAIL_LIVE_MAILCOM") != "1":
        pytest.skip("live mail.com tests disabled")
    pytest.fail("configure live credentials to enable")
