from app.services.log_redact import redact_mapping, redact_text


def test_redact_password_kv():
    s = redact_text("login failed password=supersecret token=abc")
    assert "supersecret" not in s
    assert "password=***" in s


def test_redact_long_token():
    tok = "M." + "a" * 80
    s = redact_text(f"refresh {tok}")
    assert tok not in s
    assert "***" in s


def test_redact_mapping():
    out = redact_mapping({"password": "x", "email": "a@b.com", "nested": {"api_key": "k"}})
    assert out["password"] == "***"
    assert out["email"] == "a@b.com"
    assert out["nested"]["api_key"] == "***"
