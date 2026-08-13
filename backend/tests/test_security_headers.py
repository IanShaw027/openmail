"""Parent-page CSP and CORS defaults (review P2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def test_cors_origins_default_is_empty() -> None:
    """Same-origin SPA deploy must not ship with a baked-in Vite allowlist."""
    assert Settings.model_fields["cors_origins"].default == ""


def test_empty_cors_does_not_echo_dev_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            resp = client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow = {k.lower(): v for k, v in resp.headers.items()}
        assert "access-control-allow-origin" not in allow
    finally:
        get_settings.cache_clear()


def test_explicit_cors_allows_listed_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            resp = client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        get_settings.cache_clear()


def test_csp_script_src_omits_unsafe_inline_and_cloudflare(client: TestClient) -> None:
    resp = client.get("/api/health")
    csp = resp.headers["content-security-policy"]
    directives = {p.strip().split(" ", 1)[0]: p.strip() for p in csp.split(";") if p.strip()}
    script = directives["script-src"]
    style = directives["style-src"]
    assert "'unsafe-eval'" in script
    assert "'unsafe-inline'" not in script
    assert "cloudflareinsights" not in csp
    assert "'unsafe-inline'" in style
