"""mail.com cookie provider unit tests with HTML fixtures (no live network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.providers.base import resolve_provider
from app.providers.cookie_mailcom import (
    COMPOSE_CLIENT_ID,
    COMPOSE_CLIENT_SECRET,
    COMPOSE_GRANT_TYPE,
    COMPOSE_SCOPE_W,
    MailcomCookieProvider,
    _auth_id_from_jwt,
    _build_submission_body,
    _compose_basic_auth,
    _extract_sid_from_client,
    _normalize_bearer,
    _obtain_compose_token,
    _parse_token_response,
    _sid_from_text,
    collect_messagelist_with_paging,
    extract_messagelist_next_url,
    extract_ott,
    html_indicates_bad_credentials,
    is_mailcom_login_failed_url,
    is_transient_login_error,
    normalize_mailcom_success_url,
    parse_forms,
    parse_lightmailer_message_list,
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
    # Wrong password must not be rewritten as "login unstable"
    assert is_transient_login_error("账号或密码错误") is False
    assert is_transient_login_error("mail.com 访问过于频繁或需要验证码，请稍后重试") is True


def test_logout_ls_wd_is_bad_password() -> None:
    assert is_mailcom_login_failed_url("https://www.mail.com/logout/?ls=wd") is True
    assert is_mailcom_login_failed_url("https://www.mail.com/logout/?ls=te") is True
    assert is_mailcom_login_failed_url("https://navigator-lxa.mail.com/login?ott=abc") is False
    assert html_indicates_bad_credentials("ok", "https://www.mail.com/logout/?ls=wd") is True


def test_normalize_success_url() -> None:
    assert "navigator-lxa" in normalize_mailcom_success_url(
        "https://$(clientName)-$(dataCenter).mail.com/login"
    )
    assert normalize_mailcom_success_url("https://navigator-bs.mail.com/login").endswith(
        "navigator-bs.mail.com/login"
    )


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


def test_extract_messagelist_next_url() -> None:
    page1 = _load("messagelist_page1.html")
    base = "https://lightmailer.mail.com/messagelist?folderId=INBOX&page=1"
    nxt = extract_messagelist_next_url(base, page1)
    assert nxt is not None
    assert "page=2" in nxt
    assert "messagelist" in nxt

    page2 = _load("messagelist_page2.html")
    base2 = "https://lightmailer.mail.com/messagelist?folderId=INBOX&page=2"
    assert extract_messagelist_next_url(base2, page2) is None


def test_collect_messagelist_with_paging_two_pages() -> None:
    page1 = _load("messagelist_page1.html")
    page2 = _load("messagelist_page2.html")
    base = "https://lightmailer.mail.com/messagelist?folderId=INBOX&page=1"

    class _PagingClient:
        def __init__(self) -> None:
            self.gets: list[str] = []

        def get(self, url: str, **kwargs: Any) -> _FakeResp:
            self.gets.append(url)
            if "page=2" in url:
                return _FakeResp(page2, url=url)
            return _FakeResp(page1, url=url)

    client = _PagingClient()
    # limit 3 forces following next page (page1 only has 2)
    msgs = collect_messagelist_with_paging(
        client,
        first_url=base,
        first_html=page1,
        limit=3,
        folder="inbox",
    )
    assert len(msgs) == 3
    ids = [m.id for m in msgs]
    assert "1001" in ids and "1002" in ids and "1003" in ids
    # second page fetched once
    assert any("page=2" in u for u in client.gets)

    # limit 2 stays on first page (no extra GET beyond first_html)
    client2 = _PagingClient()
    msgs2 = collect_messagelist_with_paging(
        client2,
        first_url=base,
        first_html=page1,
        limit=2,
        folder="inbox",
    )
    assert len(msgs2) == 2
    assert client2.gets == []


def test_parse_lightmailer_page1() -> None:
    msgs = parse_lightmailer_message_list(
        "https://lightmailer.mail.com/messagelist",
        _load("messagelist_page1.html"),
        limit=10,
        folder="inbox",
    )
    assert len(msgs) == 2
    assert msgs[0].id == "1001"
    assert "Newest" in msgs[0].subject


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


def test_resolve_provider_respects_explicit_imap_on_mail_com() -> None:
    acc = SimpleNamespace(provider="imap", email="u@mail.com")
    p = resolve_provider(acc)
    assert p is not None
    assert p.name == "imap"


def test_resolve_provider_respects_explicit_http_api_on_mail_com() -> None:
    acc = SimpleNamespace(provider="http_api", email="api@worker.mail.com")
    p = resolve_provider(acc)
    assert p is not None
    assert p.name == "http_api"


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


def test_full_login_path_a_logout_wd_is_bad_password() -> None:
    """Live SSO: wrong password → 303 https://www.mail.com/logout/?ls=wd"""
    provider = MailcomCookieProvider()
    home = (
        '<html><body><form method="post" action="https://login.mail.com/login">'
        '<input type="hidden" name="successURL" value="https://$(clientName)-$(dataCenter).mail.com/login"/>'
        '<input type="text" name="username"/>'
        '<input type="password" name="password"/>'
        "</form></body></html>"
    )

    class _SsoClient(_FakeClient):
        def get(self, url: str, **kwargs: Any) -> _FakeResp:
            if "www.mail.com" in url or url.rstrip("/").endswith("mail.com"):
                return _FakeResp(home, url="https://www.mail.com/")
            return super().get(url, **kwargs)

        def post(self, url: str, **kwargs: Any) -> _FakeResp:
            self.posts.append({"url": url, "data": kwargs.get("data") or {}})
            return _FakeResp(
                "<html>logout</html>",
                url="https://www.mail.com/logout/?ls=wd",
            )

    client = _SsoClient({})
    ok, err, _ = provider.full_login(client, "vita@mail.com", "wrong", site="mail.com")
    assert ok is False
    assert err == "账号或密码错误"
    assert client.posts
    # successURL placeholders expanded
    assert "navigator-lxa" in str(client.posts[0]["data"].get("successURL", ""))


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


def test_fetch_before_filters_after_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MailcomCookieProvider()
    base = "https://lightmailer.mail.com/messagelist?folderId=INBOX&page=1"

    class _PagingClient:
        def __init__(self) -> None:
            self.gets: list[str] = []

        def get(self, url: str, **kwargs: Any) -> _FakeResp:
            self.gets.append(url)
            if "page=2" in url:
                return _FakeResp(_load("messagelist_page2.html"), url=url)
            return _FakeResp(_load("messagelist_page1.html"), url=url)

    client = _PagingClient()
    monkeypatch.setattr("app.providers.cookie_mailcom._http_client", lambda *a, **k: client)
    monkeypatch.setattr(
        MailcomCookieProvider,
        "try_restore",
        lambda self, client, cookies, *, site="mail.com", meta=None: (
            True,
            {"folder_url": base, "last_probe": "restore_ok"},
        ),
    )
    monkeypatch.setattr(MailcomCookieProvider, "fetch_detail", lambda *a, **k: None)

    acc = SimpleNamespace(provider="cookie", email="user@mail.com", proxy=None)
    result = provider.fetch(
        acc,
        credentials={
            "cookies": [{"name": "sid", "value": "abc"}],
            "site": "mail.com",
        },
        limits={"before": "2026-08-03T00:30:00Z", "max_messages": 2},
        quick=False,
    )
    assert result.ok is True
    assert [m.id for m in result.messages] == ["1003", "1004"]
    assert any("page=2" in u for u in client.gets)


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


# --- cats mailsubmission / passport helpers (browser capture 2026-08) ---------


def test_sid_from_text_prefers_query_param() -> None:
    sid = (
        "3291c647bcf1d8239fdd88393df7f0af71bf3e43c17ac5c5f6fd417b61041c6beba05771"
        "f7173a221624ef7103c3dfb3"
    )
    url = f"https://navigator-lxa.mail.com/mail?sid={sid}"
    assert _sid_from_text(url) == sid
    assert _sid_from_text("https://navigator-lxa.mail.com/mail") is None
    # short navigator cookie hash must NOT match
    assert _sid_from_text("navigator=9560521fcbc8d409ea90e15a164422de") is None


def test_extract_sid_ignores_navigator_cookie() -> None:
    sid = (
        "3291c647bcf1d8239fdd88393df7f0af71bf3e43c17ac5c5f6fd417b61041c6beba05771"
        "f7173a221624ef7103c3dfb3"
    )
    client = SimpleNamespace(cookies=SimpleNamespace(jar=[], get=lambda n: "shortnavhash"))
    # only navigator cookie present → no sid
    assert _extract_sid_from_client(client, {}) is None
    # meta sid wins
    assert _extract_sid_from_client(client, {"sid": sid}) == sid
    # URL in meta
    assert (
        _extract_sid_from_client(
            client, {"navigator_url": f"https://navigator-lxa.mail.com/mail?sid={sid}"}
        )
        == sid
    )


def test_normalize_bearer_qX_prefix() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdXRoX2lkIjoiYS14In0.sig"
    assert _normalize_bearer(jwt).startswith("qX")
    assert _normalize_bearer("qX" + jwt) == "qX" + jwt
    assert _normalize_bearer("Bearer " + jwt).startswith("qX")


def test_auth_id_from_jwt() -> None:
    import base64
    import json

    payload = {
        "auth_id": "a-FzbiCxTaQ5CR0mb0_kH0kQ",
        "scope": "mail_mailbox_w",
        "client_id": "mailcom_mailcompose_passport_live",
    }
    mid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"qXhdr.{mid}.sig"
    assert _auth_id_from_jwt(token) == "a-FzbiCxTaQ5CR0mb0_kH0kQ"
    assert _auth_id_from_jwt(f"hdr.{mid}.sig") == "a-FzbiCxTaQ5CR0mb0_kH0kQ"


def test_build_submission_body_html_only_like_browser() -> None:
    body = _build_submission_body(
        from_addr="user@mail.com",
        to=["rcpt@example.com"],
        subject="Aw: hello",
        body_text="ignored when html present",
        body_html="<html><body>hi</body></html>",
    )
    assert body["mailHeader"]["messageType"] == "MAIL"
    assert body["mailHeader"]["from"] == "user@mail.com"
    assert body["mailHeader"]["to"] == ["rcpt@example.com"]
    assert body["mailHeader"]["cc"] == []
    assert body["mailHeader"]["bcc"] == []
    assert body["htmlBody"] == "<html><body>hi</body></html>"
    # browser capture: plaintextBody is null when composing HTML
    assert body["plaintextBody"] is None
    assert body["mailClientMeta"] == {"mail-drop": None}
    assert body["attachments"] == []
    assert body["transientMailProperties"] == {}


def test_build_submission_body_plain_wrapped_to_html() -> None:
    body = _build_submission_body(
        from_addr="user@mail.com",
        to=["a@b.com"],
        subject="s",
        body_text="line1\nline2",
        body_html=None,
    )
    assert body["htmlBody"] is not None
    assert "<br>" in body["htmlBody"]
    assert body["plaintextBody"] is None


def test_build_submission_body_reply() -> None:
    body = _build_submission_body(
        from_addr="user@mail.com",
        to=["a@b.com"],
        subject="Re:",
        body_text="ok",
        body_html=None,
        reply_to_id="1785837024699918732",
    )
    assert body["transientMailProperties"] == {"reply": "1785837024699918732"}


def test_parse_token_response_shapes() -> None:
    import base64
    import json

    payload = {"auth_id": "a-ABC", "scope": "mail_mailbox_w"}
    mid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    jwt = f"hdr.{mid}.sig"
    tok, aid = _parse_token_response(json.dumps({"access_token": jwt}), {})
    assert tok == jwt
    assert aid == "a-ABC"
    tok2, aid2 = _parse_token_response(
        json.dumps({"token": {"access_token": jwt, "auth_id": "a-XYZ"}}), {}
    )
    assert tok2 == jwt
    assert aid2 == "a-XYZ"


def test_compose_basic_auth_matches_capture() -> None:
    """Browser: Authorization: Basic bWFpbGNvbV9tYWlsY29tcG9zZV9wYXNzcG9ydF9saXZlOioqKioqKio="""
    import base64

    header = _compose_basic_auth()
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == f"{COMPOSE_CLIENT_ID}:{COMPOSE_CLIENT_SECRET}"
    assert decoded.endswith(":*******")


def test_obtain_compose_token_spa_grant() -> None:
    """POST ?sid=… with Basic + form grant_type=urn:mam:oauth:grant-type:spa."""
    import base64
    import json

    sid = (
        "6a6d249d8fb6acc80117dc55840255fc0904b1d78e48f8aacc5a08e3bd4e383213721567"
        "63280eb383f78324097cc7a8"
    )
    payload = {
        "auth_id": "a-IX2uygxAS7yYV_aXSRuTAw",
        "scope": COMPOSE_SCOPE_W,
        "client_id": COMPOSE_CLIENT_ID,
    }
    mid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    jwt = f"qXhdr.{mid}.sig"
    resp_body = json.dumps(
        {
            "access_token": jwt,
            "scope": COMPOSE_SCOPE_W,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200
        text = resp_body

    class _Client:
        def post(self, url: str, **kwargs: Any) -> _Resp:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["data"] = kwargs.get("data")
            return _Resp()

    token, auth_id, err = _obtain_compose_token(
        _Client(), meta={"sid": sid}, scope=COMPOSE_SCOPE_W
    )
    assert err is None
    assert token == jwt
    assert auth_id == "a-IX2uygxAS7yYV_aXSRuTAw"
    assert f"sid={sid}" in captured["url"]
    assert "oauth2/token" in captured["url"]
    assert captured["headers"]["Authorization"].startswith("Basic ")
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert captured["headers"]["Origin"] == "https://webmailer.mail.com"
    assert captured["data"]["grant_type"] == COMPOSE_GRANT_TYPE
    assert captured["data"]["scope"] == COMPOSE_SCOPE_W
    assert COMPOSE_GRANT_TYPE == "urn:mam:oauth:grant-type:spa"


def test_obtain_compose_token_requires_sid() -> None:
    class _Client:
        cookies = None

        def post(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("must not call oauthbridge without sid")

    token, auth_id, err = _obtain_compose_token(_Client(), meta={})
    assert token is None
    assert auth_id is None
    assert err is not None
    assert "sid" in err.lower()
