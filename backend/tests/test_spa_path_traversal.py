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


def test_spa_fallback_rejects_absolute_paths(tmp_path, monkeypatch):
    """An absolute path must not be joined as if it were relative."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>")

    secret = tmp_path / "secret.env"
    secret.write_text("OPENMAIL_MASTER_KEY=super-secret-value")

    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    client = TestClient(main_mod.create_app())

    for path in (
        "//etc/hosts",
        "/%2fetc%2fhosts",
        "/" + str(secret).lstrip("/"),
        "/%2f" + str(secret).lstrip("/"),
    ):
        resp = client.get(path)
        assert "super-secret-value" not in resp.text, path
        assert "<html>spa</html>" in resp.text, path


def test_spa_fallback_rejects_symlinks_out_of_the_static_root(tmp_path, monkeypatch):
    """Containment is checked after resolution, so a symlink cannot bridge out.

    This is the case that justifies resolving the candidate at all; without it a
    string-prefix check would happily serve `static/leak.env`.
    """
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>")

    secret = tmp_path / "secret.env"
    secret.write_text("OPENMAIL_MASTER_KEY=super-secret-value")
    (static_dir / "leak.env").symlink_to(secret)
    (static_dir / "up").symlink_to(tmp_path, target_is_directory=True)

    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    client = TestClient(main_mod.create_app())

    for path in ("/leak.env", "/up/secret.env"):
        resp = client.get(path)
        assert "super-secret-value" not in resp.text, path
        assert "<html>spa</html>" in resp.text, path


def test_spa_fallback_rejects_a_sibling_directory_sharing_the_prefix(tmp_path, monkeypatch):
    """`/app/static_evil/x` must not pass a check meant for `/app/static/`."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>")

    sibling = tmp_path / "static_evil"
    sibling.mkdir()
    (sibling / "secret.env").write_text("OPENMAIL_MASTER_KEY=super-secret-value")

    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    client = TestClient(main_mod.create_app())

    resp = client.get("/%2e%2e/static_evil/secret.env")
    assert "super-secret-value" not in resp.text
    assert "<html>spa</html>" in resp.text


def test_spa_fallback_survives_unparsable_paths(tmp_path, monkeypatch):
    """A path the filesystem cannot parse is a bad route, not a server error."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>")

    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    client = TestClient(main_mod.create_app())

    for path in ("/%00", "/foo%00.js", "/" + "a" * 5000):
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
        assert "<html>spa</html>" in resp.text, path
