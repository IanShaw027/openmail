"""Regression: SPA fallback must not serve files outside the static root.

Reproduces the pre-fix traversal where `GET /%2e%2e/.env` escaped
`app/static` and leaked arbitrary files (including the master key)."""

from __future__ import annotations

import app.main as main_mod
from fastapi.testclient import TestClient


def test_spa_fallback_rejects_path_traversal(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>")
    (static_dir / "app.js").write_text("console.log('real asset')")

    # Sensitive file that lives OUTSIDE the static root (sibling of it).
    secret = tmp_path / "secret.env"
    secret.write_text("OPENMAIL_MASTER_KEY=super-secret-value")

    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    client = TestClient(main_mod.create_app())

    # Encoded traversal must fall back to index.html, never leak the secret.
    for path in (
        "/%2e%2e/secret.env",
        "/foo/%2e%2e/%2e%2e/secret.env",
        "/%2e%2e%2f%2e%2e%2fsecret.env",
    ):
        resp = client.get(path)
        assert "super-secret-value" not in resp.text, path
        assert "<html>spa</html>" in resp.text, path

    # Legitimate asset and SPA route still work.
    assert client.get("/app.js").text == "console.log('real asset')"
    assert "<html>spa</html>" in client.get("/some/client/route").text
