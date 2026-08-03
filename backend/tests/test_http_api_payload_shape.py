"""HttpApi payload shape / error detection."""

from __future__ import annotations

from app.providers.http_api import _payload_has_supported_shape, _payload_is_error


def test_null_error_field_is_not_error():
    payload = {"messages": [], "error": None}
    assert _payload_is_error(payload) is False
    assert _payload_has_supported_shape(payload) is True


def test_empty_errors_list_is_not_error():
    payload = {"data": {"messages": []}, "errors": []}
    assert _payload_is_error(payload) is False
    # has "data" key → supported
    assert _payload_has_supported_shape(payload) is True


def test_truthy_error_string_is_error():
    payload = {"messages": [{"id": "1"}], "error": "upstream failed"}
    assert _payload_is_error(payload) is True
    assert _payload_has_supported_shape(payload) is False


def test_success_false_is_error():
    assert _payload_is_error({"success": False, "messages": []}) is True
    assert _payload_is_error({"ok": False}) is True


def test_empty_string_error_is_not_error():
    assert _payload_is_error({"messages": [], "error": ""}) is False
