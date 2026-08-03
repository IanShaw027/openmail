"""Public code-API by token only (no user/admin session).

Create/rotate/delete of tokens for stored server accounts is disabled
(local-first). Existing token URLs still work for legacy rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import DbDep, SettingsDep
from app.models import Account, CodeApiToken
from app.schemas import CodeApiOut, CodeFetchJsonResult
from app.services.fetch_service import FetchServiceResult, fetch_account

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
    format: str = Query(default="json"),
    refresh: int = Query(default=0),
) -> Response:
    row = db.query(CodeApiToken).filter(CodeApiToken.token == token).one_or_none()
    if row is None or not row.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")

    acc = db.get(Account, row.account_id)
    if acc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    row.last_used_at = datetime.now(timezone.utc)
    db.commit()

    use_cache = refresh != 1
    result = fetch_account(
        db,
        acc,
        folder="inbox",
        quick=True,
        force=refresh == 1,
        settings=settings,
        use_cache=use_cache,
    )

    if format in ("text", "plain"):
        body = result.code or ""
        return PlainTextResponse(body)

    payload = _result_to_json(result)
    return Response(
        content=payload.model_dump_json(by_alias=True),
        media_type="application/json",
    )
