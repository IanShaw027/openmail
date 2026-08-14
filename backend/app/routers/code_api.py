"""Public code-API by token only (no user/admin session).

Create/rotate/delete of tokens for stored server accounts is disabled
(local-first). Existing token URLs still work for legacy rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.config import Settings
from app.deps import DbDep, SettingsDep
from app.deps_device import device_id_strict
from app.models import Account, CodeApiToken
from app.schemas import CodeApiOut, CodeFetchJsonResult
from app.services.fetch_service import FetchServiceResult, fetch_account
from app.services.license import check_code_api_miss_quota, check_code_api_quota

router = APIRouter(tags=["code-api"])


def _code_url(settings: Settings, token: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/code/{token}"


@router.post(
    "/api/accounts/{account_id}/code-api",
    response_model=CodeApiOut,
    summary="create-or-return (removed with user system)",
)
def create_or_return_code_api(account_id: str) -> CodeApiOut:
    _ = account_id
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="code-api create removed — use local fetch; token URLs already issued still work",
    )


@router.post(
    "/api/accounts/{account_id}/code-api/disable",
    summary="Disable a legacy code-api token for a device-owned account",
)
def disable_code_api(
    account_id: str,
    db: DbDep,
    device_id: str = Depends(device_id_strict),
) -> dict[str, bool]:
    acc = db.get(Account, account_id)
    if acc is None or acc.owner_user_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    row = db.query(CodeApiToken).filter(CodeApiToken.account_id == account_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")
    row.enabled = False
    db.commit()
    return {"ok": True, "enabled": False}


def _client_ip(request: Request) -> str:
    """Peer address of the request.

    Intentionally ignores X-Forwarded-For: it is attacker-controlled unless a
    trusted proxy is known to overwrite it, and honouring it here would let a
    single client bypass the limit by rotating the header. Behind a reverse
    proxy every miss therefore shares one bucket, which is acceptable for a path
    that only legitimate clients never hit.
    """
    client = request.client
    return client.host if client and client.host else "unknown"


def _enforce(outcome: tuple[bool, str | None]) -> None:
    allowed, err = outcome
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=err or "rate limit exceeded",
            # The window is a rolling hour, so the earliest a slot can free up is
            # when the oldest event ages out. Without a hint, clients retry in a
            # tight loop against a limit they cannot see.
            headers={"Retry-After": "60"},
        )


def _result_to_json(result: FetchServiceResult) -> CodeFetchJsonResult:
    return CodeFetchJsonResult(
        ok=result.ok,
        code=result.code,
        email=result.email,
        subject=result.subject,
        from_=result.from_,
        date=result.date,
        folder=result.folder,
        cached=result.cached,
        error=result.error,
        message_count=result.message_count,
    )


@router.api_route(
    "/api/v1/code/{token}",
    methods=["GET", "POST"],
    summary="Public code fetch by token (legacy server accounts)",
)
def public_code_fetch(
    token: str,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    format: str | None = Query(default=None),
    refresh: int = Query(default=0),
    folder: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    _caller_regex: str | None = Query(
        default=None,
        alias="regex",
        description="Ignored. Only the token's stored default_regex is used.",
    ),
) -> Response:
    row = db.query(CodeApiToken).filter(CodeApiToken.token == token).one_or_none()
    if row is None or not row.enabled:
        # Throttle misses too, by IP: the per-token limit below can only charge
        # tokens that exist, so without this the not-found path is an unmetered
        # way to hit the endpoint (and to enumerate tokens).
        _enforce(check_code_api_miss_quota(_client_ip(request), settings=settings))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")

    acc = db.get(Account, row.account_id)
    if acc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    # Public endpoint (token-only auth): rate limit per token to stop a leaked
    # URL from hammering upstream providers / draining the proxy pool.
    #
    # Deliberately not on this request's session: the quota needs its own
    # BEGIN IMMEDIATE to serialize the count-then-insert, and SQLite ignores the
    # SELECT ... FOR UPDATE that would otherwise guard it. Sharing the session
    # skips that and lets concurrent requests all read "under the limit".
    _enforce(check_code_api_quota(token, refresh=refresh == 1, settings=settings))

    row.last_used_at = datetime.now(timezone.utc)
    db.commit()

    use_cache = refresh != 1
    folder_q = (folder or "inbox").strip() or "inbox"
    keyword_q = keyword if keyword is not None else row.default_keyword
    # Public callers must not supply a regex: a crafted pattern is ReDoS against
    # full message bodies. Only the token's stored default_regex is used.
    _ = _caller_regex
    regex_q = row.default_regex
    fmt = (format or row.default_format or "json").strip().lower()
    result = fetch_account(
        db,
        acc,
        folder=folder_q,
        quick=True,
        force=refresh == 1,
        keyword=keyword_q,
        custom_regex=regex_q,
        settings=settings,
        use_cache=use_cache,
        egress_mode="bulk",
    )

    if fmt in ("text", "plain"):
        body = result.code or ""
        return PlainTextResponse(body)

    payload = _result_to_json(result)
    return Response(
        content=payload.model_dump_json(by_alias=True),
        media_type="application/json",
    )
