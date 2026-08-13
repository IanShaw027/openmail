"""License token comparison must not 500 on length mismatch."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.license import is_licensed


def test_is_licensed_short_hmac_token_is_false() -> None:
    settings = SimpleNamespace(
        license_token_set=set(),
        license_hmac_secret="super-secret",
    )
    assert (
        is_licensed(device_id="vk_abc", license_token="short", settings=settings) is False
    )
