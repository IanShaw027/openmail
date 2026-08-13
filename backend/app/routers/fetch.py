"""Fetch endpoints: proxy (credentials in body) + device-owned stored accounts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.deps import DbDep, SettingsDep
from app.deps_device import device_id_quota, device_id_strict
from app.models import Account
from app.schemas import (
    FetchMessageOut,
    FetchResponse,
    ProxyFetchRequest,
    SendMailRequest,
    SendMailResponse,
)
from app.services.fetch_service import fetch_account, fetch_proxy
from app.services.license import check_poll_quota
from app.services.mail_slim import slim_message_fields
from app.services.send_service import send_mail

router = APIRouter(tags=["fetch"])


def _messages_out(messages: list[Any]) -> list[FetchMessageOut]:
    """Serialize messages for the client with server-side body slimming.

    Large marketing HTML (base64 images, scripts) blows browser vault
    localStorage when encrypted — strip bloat here before JSON leaves the API.
    """
    out: list[FetchMessageOut] = []
    for m in messages:
        body_html, body_text, body_preview = slim_message_fields(
            body_html=getattr(m, "body_html", None) or None,
            body_text=getattr(m, "body_text", None) or None,
            body_preview=getattr(m, "body_preview", None) or None,
        )
        uv = getattr(m, "uidvalidity", None)
        try:
            uv_out = int(uv) if uv is not None else None
        except (TypeError, ValueError):
            uv_out = None
        out.append(
            FetchMessageOut(
                id=getattr(m, "id", "") or "",
                subject=getattr(m, "subject", None) or None,
                from_=getattr(m, "from_", None) or None,
                from_address=getattr(m, "from_address", None) or None,
                to=getattr(m, "to", None) or None,
                date=getattr(m, "date", None),
                body_preview=body_preview,
                body_text=body_text,
                body_html=body_html,
                verification_code=getattr(m, "verification_code", None),
                folder=getattr(m, "folder", None) or None,
                uidvalidity=uv_out,
            )
        )
    return out


def _to_response(result: Any) -> FetchResponse:
    uv = getattr(result, "uidvalidity", None)
    try:
        uv_out = int(uv) if uv is not None else None
    except (TypeError, ValueError):
        uv_out = None
    return FetchResponse(
        ok=result.ok,
        messages=_messages_out(result.messages or []),
        message_count=result.message_count or len(result.messages or []),
        folder=result.folder or "inbox",
        fetched_at=result.fetched_at,
        code=result.code,
        cached=result.cached,
        error=result.error,
        email=result.email,
        account_id=result.account_id,
        subject=result.subject,
        from_=result.from_,
        date=result.date,
        retry_after=result.retry_after,
        session_cookies=getattr(result, "session_cookies", None),
        session_meta=getattr(result, "session_meta", None),
        session_restored=bool(getattr(result, "session_restored", False)),
        mailboxes=getattr(result, "mailboxes", None),
        uidvalidity=uv_out,
        credential_updates=getattr(result, "credential_updates", None),
    )


@router.post(
    "/api/accounts/{account_id}/fetch",
    response_model=FetchResponse,
    summary="Fetch mail for a device-owned cloud account",
)
def fetch_stored_account(
    account_id: str,
    db: DbDep,
    settings: SettingsDep,
    device_id: str = Depends(device_id_strict),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
    folder: str = "inbox",
    quick: bool = True,
    since: str | None = None,
    before: str | None = None,
    max_messages: int | None = None,
    full: bool = False,
) -> FetchResponse:
    ok_q, qerr = check_poll_quota(
        device_id, license_token=x_license_token, settings=settings
    )
    if not ok_q:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=qerr)
    acc = db.get(Account, account_id)
    if acc is None or acc.owner_user_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    result = fetch_account(
        db,
        acc,
        folder=folder or "inbox",
        quick=bool(quick),
        settings=settings,
        since=since,
        before=before,
        max_messages=max_messages,
        full=bool(full),
    )
    db.commit()
    return _to_response(result)


@router.post(
    "/api/fetch/proxy",
    response_model=FetchResponse,
    summary="Proxy fetch (credentials in body, not stored)",
)
def proxy_fetch(
    body: ProxyFetchRequest,
    settings: SettingsDep,
    device_id: str = Depends(device_id_quota),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> FetchResponse:
    ok_q, qerr = check_poll_quota(
        device_id, license_token=x_license_token, settings=settings
    )
    if not ok_q:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=qerr)
    result = fetch_proxy(
        email=body.email.strip().lower(),
        provider=body.provider,
        password=body.password,
        credential=body.credential,
        cookies=body.cookies,
        folder=body.folder or "inbox",
        quick=bool(body.quick),
        keyword=body.keyword,
        custom_regex=body.regex,
        settings=settings,
        proxy=body.proxy,
        since=body.since,
        before=body.before,
        max_messages=body.max_messages,
        full=bool(body.full),
    )
    return _to_response(result)


@router.post(
    "/api/fetch/send",
    response_model=SendMailResponse,
    summary="Send mail with credentials in body",
)
def proxy_send(
    body: SendMailRequest,
    settings: SettingsDep,
    device_id: str = Depends(device_id_quota),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> SendMailResponse:
    # Same poll quota as fetch — prevents open relay abuse
    ok_q, qerr = check_poll_quota(
        device_id, license_token=x_license_token, settings=settings
    )
    if not ok_q:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=qerr)
    if not body.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email required")
    if not body.to:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="to required")
    # Cap recipients
    if len(body.to) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="too many recipients (max 20)",
        )
    provider = body.provider or "imap"
    result = send_mail(
        email=body.email.strip().lower(),
        provider=str(getattr(provider, "value", provider)),
        password=body.password,
        credential=body.credential,
        to=body.to,
        subject=body.subject or "",
        body_text=body.body_text or "",
        body_html=body.body_html,
        proxy=body.proxy,
    )
    return SendMailResponse(
        ok=result.ok,
        error=result.error,
        detail=result.detail,
        credential_updates=getattr(result, "credential_updates", None),
    )


@router.post(
    "/api/accounts/{account_id}/send",
    response_model=SendMailResponse,
    summary="Stored send (removed)",
)
def send_stored_account(account_id: str) -> SendMailResponse:
    _ = account_id
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="use POST /api/fetch/send with credentials in body",
    )
