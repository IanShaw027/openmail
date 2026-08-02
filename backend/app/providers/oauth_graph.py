"""Microsoft Graph OAuth provider (refresh_token + client_id).

Flow:
1. POST login.microsoftonline.com/.../token with grant_type=refresh_token
2. GET graph.microsoft.com/v1.0/me/mailFolders/{inbox|junkemail}/messages
3. Optionally expand body for top messages
4. Map to Message; return CredentialUpdates if new refresh_token
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

_SELECT = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead"
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
    body_text = content if content_type == "text" else ""
    body_html = content if content_type == "html" else (content if not body_text else "")
    preview = str(item.get("bodyPreview") or "")
    msg = Message(
        id=str(item.get("id") or ""),
        subject=str(item.get("subject") or ""),
        from_=display,
        from_address=address,
        to=_format_to(item.get("toRecipients")),
        date=item.get("receivedDateTime"),
        body_preview=preview,
        body_text=body_text or preview,
        body_html=body_html if content_type == "html" else "",
        folder=folder,
        raw_refs={"graph_id": item.get("id")},
    )
    annotate_message_code(msg)
    return msg


class OAuthGraphProvider:
    """Real Microsoft Graph OAuth provider."""

    name = "oauth"

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
            raise OAuthError(_map_token_error(resp.status_code, body), status=resp.status_code)
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

        try:
            token_body = self.refresh_access_token(
                client_id=client_id,
                refresh_token=refresh_token,
                proxy=proxy_str,
            )
        except OAuthError as exc:
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

        access_token = str(token_body["access_token"])
        new_refresh = token_body.get("refresh_token")
        updates = CredentialUpdates(
            access_token=access_token,
            refresh_token=str(new_refresh) if new_refresh else None,
        )
        # Also surface expires if present (provider doesn't persist expires itself)
        expires_in = token_body.get("expires_in")
        if expires_in is not None:
            updates.session_meta = {
                "token_expires_in": expires_in,
                "token_obtained_at": datetime.now(timezone.utc).isoformat(),
            }

        headers = {"Authorization": f"Bearer {access_token}"}
        # Incremental / older-page filters
        since = None
        before = None
        if limits:
            since = limits.get("since") or limits.get("received_after")
            before = limits.get("before") or limits.get("received_before")
        filters: list[str] = []
        if since and not before:
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

        client = self._http(proxy=proxy_str)
        close = self._owns_client()
        try:
            try:
                resp = client.get(list_url, headers=headers)
            except httpx.TimeoutException:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error="取件超时，请重试 / Fetch timed out, please retry",
                    credential_updates=updates if updates.any() else None,
                )
            except httpx.HTTPError as exc:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error=f"网络错误 / Network error: {exc.__class__.__name__}",
                    credential_updates=updates if updates.any() else None,
                )

            if resp.status_code >= 400:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error=_map_graph_error(resp.status_code),
                    credential_updates=updates if updates.any() else None,
                )

            try:
                payload = resp.json()
            except Exception:
                return FetchResult(
                    ok=False,
                    folder=out_folder,
                    error="Graph 返回非 JSON / Graph returned non-JSON",
                    credential_updates=updates if updates.any() else None,
                )

            items = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                items = []

            messages: list[Message] = []
            # Fetch body for top few (quick: 3, full: min(top, 10))
            body_limit = 3 if quick else min(top, 10)
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                msg_id = item.get("id")
                if msg_id and i < body_limit:
                    try:
                        br = client.get(
                            f"{GRAPH_BASE}/me/messages/{msg_id}"
                            f"?$select=id,subject,from,toRecipients,receivedDateTime,bodyPreview,body",
                            headers=headers,
                        )
                        if br.status_code < 400:
                            detailed = br.json()
                            if isinstance(detailed, dict):
                                item = {**item, **detailed}
                    except httpx.HTTPError:
                        pass
                messages.append(_graph_message_to_message(item, folder=out_folder))

            return FetchResult(
                ok=True,
                messages=messages,
                folder=out_folder,
                credential_updates=updates if updates.any() else None,
            )
        finally:
            if close:
                client.close()

    def health(self, account: Any, *, credentials: dict[str, Any] | None = None) -> HealthResult:
        creds = dict(credentials or {})
        client_id = str(creds.get("client_id") or "").strip()
        refresh_token = str(creds.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            return HealthResult(ok=False, detail="missing client_id or refresh_token")
        try:
            self.refresh_access_token(client_id=client_id, refresh_token=refresh_token)
            return HealthResult(ok=True, detail="token refresh ok")
        except OAuthError as exc:
            return HealthResult(ok=False, detail=str(exc))
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, detail=str(exc))


class OAuthError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)
