"""Graph send must return a rotated refresh_token so the client can persist it."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.send_service import send_via_graph


def test_send_via_graph_returns_rotated_refresh_token() -> None:
    token_body = {
        "access_token": "at_new",
        "refresh_token": "rt_rotated",
        "expires_in": 3600,
    }
    with patch(
        "app.providers.oauth_graph.OAuthGraphProvider.refresh_access_token",
        return_value=token_body,
    ):
        with patch("httpx.Client") as client_cls:
            inst = MagicMock()
            inst.post.return_value.status_code = 202
            client_cls.return_value.__enter__.return_value = inst
            result = send_via_graph(
                client_id="cid",
                refresh_token="rt_old",
                to=["a@b.com"],
                subject="hi",
                body_text="x",
            )
    assert result.ok is True
    assert result.credential_updates is not None
    assert result.credential_updates.get("refresh_token") == "rt_rotated"
    assert result.credential_updates.get("access_token") == "at_new"


def test_send_via_graph_omits_updates_when_token_not_rotated() -> None:
    token_body = {"access_token": "at_only"}
    with patch(
        "app.providers.oauth_graph.OAuthGraphProvider.refresh_access_token",
        return_value=token_body,
    ):
        with patch("httpx.Client") as client_cls:
            inst = MagicMock()
            inst.post.return_value.status_code = 202
            client_cls.return_value.__enter__.return_value = inst
            result = send_via_graph(
                client_id="cid",
                refresh_token="rt_old",
                to=["a@b.com"],
                subject="hi",
                body_text="x",
            )
    assert result.ok is True
    # access_token is still useful for the client to ignore; refresh stays absent
    updates = result.credential_updates or {}
    assert "refresh_token" not in updates
