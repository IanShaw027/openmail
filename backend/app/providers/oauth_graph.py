"""Microsoft Graph OAuth provider (refresh_token + client_id).

Flow:
1. POST login.microsoftonline.com/.../token with grant_type=refresh_token
2. GET graph.microsoft.com/v1.0/me/mailFolders/{inbox|junkemail}/messages
3. Map to Message (list includes body; fill gaps per-message)
4. Return CredentialUpdates if new refresh_token
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.providers.base import (
    CredentialUpdates,
    FetchResult,
    HealthResult,
    Message,
)
from app.services.parser import annotate_message_code

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
# Thunderbird / mail-public style public client: refresh tokens are IMAP/POP, not Graph.
THUNDERBIRD_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
IMAP_FIRST_CLIENT_IDS = frozenset({THUNDERBIRD_CLIENT_ID.lower()})
DEFAULT_MS_IMAP_HOST = "outlook.office365.com"

# folder query param → Graph well-known folder name
_FOLDER_MAP = {
    "inbox": "inbox",
    "junk": "junkemail",
    "junkemail": "junkemail",
    "spam": "junkemail",
    "sent": "sentitems",
    "sentitems": "sentitems",
    "sent mail": "sentitems",
}

# Request body on the list so OTP in HTML is not dropped after row 25.
# Per-message GET still fills any row Graph omitted.
# See https://learn.microsoft.com/en-us/graph/api/resources/message
_SELECT = (
    "id,subject,from,toRecipients,receivedDateTime,bodyPreview,"
    "isRead,hasAttachments,body,uniqueBody"
)
_TIMEOUT = 30.0


def _provider_value(account: Any) -> str:
    p = getattr(account, "provider", None)
    if p is None:
        return ""
    return str(getattr(p, "value", p))


def _map_token_error(status: int, body: dict[str, Any] | str) -> str:
    err = ""
    desc = ""
    if isinstance(body, dict):
        err = str(body.get("error") or "")
        desc = str(body.get("error_description") or body.get("error_codes") or "")
    text = f"{err} {desc}".lower()
    if "70000" in text or "invalid_grant" in text or "expired" in text:
        return "刷新令牌无效或已过期 / Refresh token invalid or expired"
    if status in (401, 403) or "unauthorized" in text or "forbidden" in text:
        return "权限不足或令牌失效 / Insufficient permission or token invalid"
    if status >= 500:
        return "微软认证服务暂时不可用 / Microsoft auth temporarily unavailable"
    return "OAuth 令牌刷新失败 / OAuth token refresh failed"


def _map_graph_error(status: int) -> str:
    if status in (401, 403):
        return "Graph 权限不足或令牌失效 / Graph permission denied or token invalid"
    if status == 404:
        return "邮箱文件夹不存在 / Mail folder not found"
    if status >= 500:
        return "Microsoft Graph 暂时不可用 / Microsoft Graph temporarily unavailable"
    return f"Graph 请求失败 ({status}) / Graph request failed ({status})"


def prefers_imap_transport(client_id: str, creds: dict[str, Any] | None = None) -> bool:
    """True when this client/token is known to be Outlook IMAP, not Graph."""
    cid = str(client_id or "").strip().lower()
    if cid in IMAP_FIRST_CLIENT_IDS:
        return True
    transport = str((creds or {}).get("oauth_transport") or "").strip().lower()
    return transport == "imap"


def _is_invalid_grant(exc: OAuthError) -> bool:
    if str(getattr(exc, "error", "") or "").lower() == "invalid_grant":
        return True
    text = str(exc).lower()
    return "invalid_grant" in text or "刷新令牌" in text or "refresh token" in text


def _token_updates(token_body: dict[str, Any], *, transport: str) -> CredentialUpdates:
    new_refresh = token_body.get("refresh_token")
    updates = CredentialUpdates(
        access_token=str(token_body["access_token"]),
        refresh_token=str(new_refresh) if new_refresh else None,
    )
    meta: dict[str, Any] = {"oauth_transport": transport}
    expires_in = token_body.get("expires_in")
    if expires_in is not None:
        meta["token_expires_in"] = expires_in
        meta["token_obtained_at"] = datetime.now(timezone.utc).isoformat()
    updates.session_meta = meta
    return updates


def _format_from(from_obj: dict[str, Any] | None) -> tuple[str, str]:
    if not from_obj:
        return "", ""
    ea = from_obj.get("emailAddress") or {}
    name = str(ea.get("name") or "")
    address = str(ea.get("address") or "")
    display = name if name else address
    if name and address:
        display = f"{name} <{address}>"
    return display, address


def _format_to(recipients: list[dict[str, Any]] | None) -> str:
    if not recipients:
        return ""
    parts: list[str] = []
    for r in recipients:
        ea = (r or {}).get("emailAddress") or {}
        addr = ea.get("address") or ""
        name = ea.get("name") or ""
        if name and addr:
            parts.append(f"{name} <{addr}>")
        elif addr:
            parts.append(str(addr))
    return ", ".join(parts)


def _graph_message_to_message(item: dict[str, Any], *, folder: str) -> Message:
    display, address = _format_from(item.get("from"))
    body = item.get("body") or {}
    content = str(body.get("content") or "")
    content_type = str(body.get("contentType") or "").lower()
    # Graph returns body as { contentType: "html"|"text", content: "..." }
    body_html = ""
    body_text = ""
    if content:
        if content_type == "text":
            body_text = content
        else:
            # default / html / unknown → treat as HTML when markup present
            if content_type == "html" or "<" in content:
                body_html = content
            else:
                body_text = content
    # uniqueBody (if ever requested) — prefer as HTML when available
    ub = item.get("uniqueBody") or {}
    if isinstance(ub, dict) and ub.get("content") and not body_html and not body_text:
        uc = str(ub.get("content") or "")
        ut = str(ub.get("contentType") or "").lower()
        if ut == "html" or "<" in uc:
            body_html = uc
        else:
            body_text = uc
    preview = str(item.get("bodyPreview") or "")
    if body_html and not body_text:
        import re

        body_text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", body_html)
        body_text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", body_text)
        body_text = re.sub(r"<[^>]+>", " ", body_text)
        body_text = re.sub(r"\s+", " ", body_text).strip()
    msg = Message(
        id=str(item.get("id") or ""),
        subject=str(item.get("subject") or ""),
        from_=display,
        from_address=address,
        to=_format_to(item.get("toRecipients")),
        date=item.get("receivedDateTime"),
        body_preview=preview or (body_text[:280] if body_text else ""),
        body_text=body_text or preview,
        body_html=body_html,
        folder=folder,
        raw_refs={"graph_id": item.get("id")},
    )
    annotate_message_code(msg)
    return msg


class OAuthGraphProvider:
    """Real Microsoft Graph OAuth provider."""

    name = "oauth"
    time_paging = "since_before"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def can_handle(self, account: Any) -> bool:
        return _provider_value(account) == "oauth"

    def _http(self, *, proxy: str | None = None) -> httpx.Client:
        if self._client is not None:
            return self._client
        kwargs: dict[str, Any] = {"timeout": _TIMEOUT}
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

    def _owns_client(self) -> bool:
        return self._client is None

    def refresh_access_token(
        self,
        *,
        client_id: str,
        refresh_token: str,
        scope: str = DEFAULT_SCOPE,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        """Exchange refresh_token for access_token. Raises httpx.HTTPError on transport fail."""
        data = {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": scope,
        }
        client = self._http(proxy=proxy)
        close = self._owns_client()
        try:
            resp = client.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        finally:
            if close:
                client.close()

        try:
            body = resp.json()
        except Exception:
            body = {"error": resp.text[:200]}

        if resp.status_code >= 400:
            err = str(body.get("error") or "") if isinstance(body, dict) else ""
            raise OAuthError(
                _map_token_error(resp.status_code, body),
                status=resp.status_code,
                error=err,
            )
        if not isinstance(body, dict) or not body.get("access_token"):
            raise OAuthError("OAuth 响应缺少 access_token / Missing access_token", status=resp.status_code)
        return body

    def fetch(
        self,
        account: Any,
        *,
        folder: str = "inbox",
        quick: bool = True,
        limits: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
    ) -> FetchResult:
        creds = dict(credentials or {})
        client_id = str(creds.get("client_id") or "").strip()
        refresh_token = str(creds.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            return FetchResult(
                ok=False,
                folder=folder,
                error="缺少 client_id 或 refresh_token / Missing client_id or refresh_token",
            )

        proxy = creds.get("proxy") or getattr(account, "proxy", None)
        proxy_str = str(proxy).strip() if proxy else None

        if limits and ("top" in limits or "max_messages" in limits):
            try:
                top = int((limits or {}).get("top") or (limits or {}).get("max_messages") or 20)
            except (TypeError, ValueError):
                top = 5 if quick else 20
        else:
            top = 5 if quick else int((limits or {}).get("top", 20))
        top = max(1, min(top, 50))
        graph_folder = _FOLDER_MAP.get((folder or "inbox").lower(), "inbox")
        if graph_folder == "junkemail":
            out_folder = "spam"
        elif graph_folder == "sentitems":
            out_folder = "sent"
        else:
            out_folder = "inbox"

        modes = ("imap", "graph") if prefers_imap_transport(client_id, creds) else ("graph", "imap")
        last_oauth: OAuthError | None = None
        for idx, mode in enumerate(modes):
            try:
                if mode == "imap":
                    return self._fetch_via_imap(
                        account,
                        folder=folder,
                        quick=quick,
                        limits=limits,
                        credentials=creds,
                        client_id=client_id,
                        refresh_token=refresh_token,
                        proxy=proxy_str,
                    )
                return self._fetch_via_graph(
                    folder=out_folder,
                    graph_folder=graph_folder,
                    top=top,
                    limits=limits,
                    client_id=client_id,
                    refresh_token=refresh_token,
                    proxy=proxy_str,
                )
            except OAuthError as exc:
                last_oauth = exc
                if idx + 1 < len(modes) and _is_invalid_grant(exc):
                    continue
                return FetchResult(ok=False, folder=out_folder, error=str(exc))
            except httpx.TimeoutException:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error="取件超时，请重试 / Fetch timed out, please retry",
                )
            except httpx.HTTPError as exc:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error=f"网络错误 / Network error: {exc.__class__.__name__}",
                )
        return FetchResult(
            ok=False,
            folder=out_folder,
            error=str(last_oauth) if last_oauth else "OAuth 令牌刷新失败 / OAuth token refresh failed",
        )

    def _fetch_via_graph(
        self,
        *,
        folder: str,
        graph_folder: str,
        top: int,
        limits: dict[str, Any] | None,
        client_id: str,
        refresh_token: str,
        proxy: str | None,
    ) -> FetchResult:
        token_body = self.refresh_access_token(
            client_id=client_id,
            refresh_token=refresh_token,
            proxy=proxy,
        )
        access_token = str(token_body["access_token"])
        updates = _token_updates(token_body, transport="graph")
        headers = {"Authorization": f"Bearer {access_token}"}
        # Incremental / older-page filters
        since = None
        before = None
        if limits:
            since = limits.get("since") or limits.get("received_after")
            before = limits.get("before") or limits.get("received_before")
        filters: list[str] = []
        if since:
            since_s = str(since).replace("+00:00", "Z")
            if "T" not in since_s:
                since_s = f"{since_s}T00:00:00Z"
            filters.append(f"receivedDateTime ge {since_s}")
        if before:
            before_s = str(before).replace("+00:00", "Z")
            if "T" not in before_s:
                before_s = f"{before_s}T00:00:00Z"
            filters.append(f"receivedDateTime lt {before_s}")
        filter_q = f"&$filter={' and '.join(filters)}" if filters else ""
        list_url = (
            f"{GRAPH_BASE}/me/mailFolders/{graph_folder}/messages"
            f"?$top={top}&$select={_SELECT}&$orderby=receivedDateTime desc{filter_q}"
        )

        client = self._http(proxy=proxy)
        close = self._owns_client()
        try:
            try:
                resp = client.get(list_url, headers=headers)
            except httpx.TimeoutException:
                return FetchResult(
                    ok=False,
                    folder=folder,
                    error="取件超时，请重试 / Fetch timed out, please retry",
                    credential_updates=updates if updates.any() else None,
                )
            except httpx.HTTPError as exc:
                return FetchResult(
                    ok=False,
                    folder=folder,
                    error=f"网络错误 / Network error: {exc.__class__.__name__}",
                    credential_updates=updates if updates.any() else None,
                )

            if resp.status_code >= 400:
                return FetchResult(
                    ok=False,
                    folder=folder,
                    error=_map_graph_error(resp.status_code),
                    credential_updates=updates if updates.any() else None,
                )

            try:
                payload = resp.json()
            except Exception:
                return FetchResult(
                    ok=False,
                    folder=folder,
                    error="Graph 返回非 JSON / Graph returned non-JSON",
                    credential_updates=updates if updates.any() else None,
                )

            items = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                items = []

            messages: list[Message] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                msg_id = item.get("id")
                body_obj = item.get("body") if isinstance(item.get("body"), dict) else {}
                has_body = bool(str(body_obj.get("content") or "").strip())
                if msg_id and not has_body:
                    try:
                        br = client.get(
                            f"{GRAPH_BASE}/me/messages/{msg_id}"
                            f"?$select=id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,uniqueBody",
                            headers=headers,
                        )
                        if br.status_code < 400:
                            detailed = br.json()
                            if isinstance(detailed, dict):
                                item = {**item, **detailed}
                    except httpx.HTTPError:
                        pass
                messages.append(_graph_message_to_message(item, folder=folder))

            return FetchResult(
                ok=True,
                messages=messages,
                folder=folder,
                credential_updates=updates if updates.any() else None,
            )
        finally:
            if close:
                client.close()

    def _fetch_via_imap(
        self,
        account: Any,
        *,
        folder: str,
        quick: bool,
        limits: dict[str, Any] | None,
        credentials: dict[str, Any],
        client_id: str,
        refresh_token: str,
        proxy: str | None,
    ) -> FetchResult:
        token_body = self.refresh_access_token(
            client_id=client_id,
            refresh_token=refresh_token,
            scope=IMAP_SCOPE,
            proxy=proxy,
        )
        updates = _token_updates(token_body, transport="imap")
        from app.providers.imap_provider import ImapProvider

        imap_creds = dict(credentials)
        imap_creds["access_token"] = str(token_body["access_token"])
        if not imap_creds.get("imap_host") and not imap_creds.get("host"):
            imap_creds["imap_host"] = DEFAULT_MS_IMAP_HOST
            imap_creds["imap_port"] = imap_creds.get("imap_port") or 993
        result = ImapProvider().fetch(
            account,
            folder=folder,
            quick=quick,
            limits=limits,
            credentials=imap_creds,
        )
        if result.credential_updates and result.credential_updates.any():
            merged = result.credential_updates
            if updates.refresh_token:
                merged.refresh_token = updates.refresh_token
            if updates.access_token:
                merged.access_token = updates.access_token
            meta = dict(merged.session_meta or {})
            meta.update(updates.session_meta or {})
            merged.session_meta = meta
            result.credential_updates = merged
        else:
            result.credential_updates = updates if updates.any() else None
        return result

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        creds = dict(credentials or {})
        client_id = str(creds.get("client_id") or "").strip()
        refresh_token = str(creds.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            return HealthResult(ok=False, detail="missing client_id or refresh_token")
        scopes = (IMAP_SCOPE, DEFAULT_SCOPE) if prefers_imap_transport(client_id, creds) else (DEFAULT_SCOPE, IMAP_SCOPE)
        last: OAuthError | httpx.HTTPError | None = None
        for idx, scope in enumerate(scopes):
            try:
                self.refresh_access_token(
                    client_id=client_id,
                    refresh_token=refresh_token,
                    scope=scope,
                )
                return HealthResult(ok=True, detail="token refresh ok")
            except OAuthError as exc:
                last = exc
                if idx + 1 < len(scopes) and _is_invalid_grant(exc):
                    continue
                return HealthResult(ok=False, detail=str(exc))
            except httpx.HTTPError as exc:
                return HealthResult(ok=False, detail=str(exc))
        return HealthResult(ok=False, detail=str(last) if last else "token refresh failed")


class OAuthError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.error = error
        super().__init__(message)
