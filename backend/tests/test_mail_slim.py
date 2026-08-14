"""Server-side mail body slimming for browser vault quota."""

from app.services.mail_slim import (
    HTML_HARD_MAX,
    TEXT_HARD_MAX,
    html_to_plain,
    slim_html,
    slim_message_fields,
    slim_text,
)


def test_strips_script_and_base64_image_but_keeps_layout_css():
    huge_b64 = "A" * 5000
    raw = f"""
    <html><head><style>.x{{color:red}}</style>
    <script>alert(1)</script></head>
    <body>
      <p style="font-size:16px">Your code is 482910</p>
      <img src="data:image/png;base64,{huge_b64}" width="600"/>
      <img width="1" height="1" src="https://track.example/pixel?x=1"/>
    </body></html>
    """
    out = slim_html(raw)
    assert out is not None
    assert "<script" not in out.lower()
    assert "data:image" not in out
    assert "482910" in out
    assert "<style>" in out.lower()
    assert "font-size:16px" in out
    assert len(out) < len(raw)


def test_hard_cap_html():
    blob = "<p>" + ("hello world " * 20_000) + "</p>"
    out = slim_html(blob)
    assert out is not None
    assert len(out) <= HTML_HARD_MAX + 40  # truncation marker
    assert "openmail:truncated" in out


def test_text_cap():
    t = "line\n" * 50_000
    out = slim_text(t)
    assert out is not None
    assert len(out) <= TEXT_HARD_MAX + 1


def test_fields_derive_text_from_html_when_missing():
    html = "<p>Verify: <b>112233</b></p>" + ("x" * 100)
    h, t, p = slim_message_fields(body_html=html, body_text=None, body_preview=None)
    assert h is not None
    assert t is not None
    assert "112233" in t
    assert p is not None
    assert len(p) <= 280


def test_html_to_plain_basic():
    assert "hi" in html_to_plain("<b>hi</b> there").lower()
