"""OpenMail FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import get_settings
from app.db import init_db
from app.routers import accounts, code_api, device, fetch, health, mails, public_config, sync, transfer
from app.services.sync_worker import start_sync_worker, stop_sync_worker


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """CSP + baseline security headers for SPA + API.

    Compatibility (cannot use ultra-strict script-src yet):
    - vue-i18n compiles messages via new Function → requires 'unsafe-eval'
    The SPA does not load Cloudflare Web Analytics, so script-src must not
    include 'unsafe-inline' or insights hosts.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = await call_next(request)
        # Force-set so we replace any previous too-strict CSP from older deploys
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "media-src 'self' blob:; "
            "worker-src 'self' blob:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'"
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(self), microphone=(), geolocation=()",
        )
        # HSTS only meaningful behind HTTPS terminators
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

# Built SPA assets live here in Docker (copied from frontend/dist).
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # Device HMAC registry (encrypted on disk) — survive restarts.
    # Fail closed: a decrypt miss must not look like an empty registry.
    from app.services.device_auth import load_registry

    load_registry()
    # After master-key rotation: re-encrypt rows decryptable via FALLBACKS
    try:
        from app.db import SessionLocal
        from app.services.crypto_migrate import migrate_reencrypt_all

        db = SessionLocal()
        try:
            migrate_reencrypt_all(db)
        finally:
            db.close()
    except Exception:
        # Non-fatal for SQL rows: worker can still decrypt via fallbacks.
        # Registry rewrite failure is logged inside migrate and re-raised;
        # if we land here the SQL walk failed after a successful load.
        pass
    # Background hourly sync (daemon thread); no-op if already running
    start_sync_worker()
    try:
        yield
    finally:
        stop_sync_worker()


def _mount_spa(application: FastAPI) -> None:
    """Serve the Vite SPA when static/index.html is present (production / Docker).

    - /assets/* → hashed JS/CSS bundles
    - other non-API GET paths → index.html (client-side router)
    API routes, /docs, /openapi.json, /health remain handled by FastAPI.
    """
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return

    # Fixed for the process lifetime; resolving it per request only re-walked
    # the same symlinks on every 404.
    static_root = STATIC_DIR.resolve()

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
        )

    @application.get("/")
    async def spa_root() -> FileResponse:
        return FileResponse(index)

    @application.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        # Never shadow API / OpenAPI / health (registered earlier; this is a safety net)
        if (
            full_path.startswith("api/")
            or full_path in {"docs", "redoc", "openapi.json", "health"}
            or full_path.startswith("docs/")
            or full_path.startswith("redoc/")
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            try:
                candidate = (static_root / full_path).resolve()
                # Reject any path that escapes the static root (e.g. `%2e%2e/.env`).
                if candidate == static_root or static_root in candidate.parents:
                    if candidate.is_file():
                        return FileResponse(candidate)
            except (OSError, ValueError):
                # Paths the filesystem refuses to even parse (embedded null byte,
                # over-long component) are not routes; serve the SPA rather than
                # turning a malformed URL into a 500.
                pass
        return FileResponse(index)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="OpenMail",
        description="Self-hosted multi-mailbox fetch console API",
        version=__version__,
        lifespan=lifespan,
    )

    # Same-origin SPA deploy: leave CORS_ORIGINS empty (no cross-origin needed).
    # Dev split (Vite :5173 → API :8000): set explicit origins in .env.
    cors_origins = settings.cors_origin_list
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.add_middleware(SecurityHeadersMiddleware)

    # No user/admin auth — local-first vault + device license (see /api/config/public)
    application.include_router(health.router)
    application.include_router(public_config.router)
    application.include_router(device.router)
    application.include_router(accounts.router)
    application.include_router(code_api.router)
    application.include_router(fetch.router)
    application.include_router(mails.router)
    application.include_router(sync.router)
    application.include_router(transfer.router)

    _mount_spa(application)

    return application


app = create_app()
