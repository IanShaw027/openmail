"""Slim mail bodies before they leave the API.

Browser vault stores encrypted messages in localStorage (~5MB per origin).
Marketing HTML with inline base64 images / tracking scripts is the main bloat.
We keep enough HTML for readable display and OTP parsing; drop the rest server-side.
"""

from __future__ import annotations

import re
from typing import Any

# Soft: only then drop class/id / huge attrs. Keep layout CSS below this.
HTML_SOFT_BYTES = 80_000
# Hard caps after slimming (chars). Quota persist can still drop HTML later.
HTML_HARD_MAX = 120_000
TEXT_HARD_MAX = 24_000
PREVIEW_MAX = 280

# Always drop these blocks (case-insensitive, non-greedy enough for mail)
_RE_SCRIPT = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
_RE_STYLE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
_RE_SVG = re.compile(r"(?is)<svg\b[^>]*>.*?</svg>")
_RE_NOSCRIPT = re.compile(r"(?is)<noscript\b[^>]*>.*?</noscript>")
_RE_COMMENT = re.compile(r"(?is)<!--.*?-->")
# Inline base64 / data-URI images (main quota killer)
_RE_DATA_URI = re.compile(
    r"""(?is)(?:src|href|background|data-src|poster)\s*=\s*(['"])\s*data:[^'"]{200,}\1"""
)
_RE_DATA_URI_CSS = re.compile(r"(?is)url\(\s*['\"]?data:[^)]{200,}\)")
# Tracking 1x1 / beacon pixels often use long query strings
_RE_TRACKING_IMG = re.compile(
    r"""(?is)<img\b[^>]*(?:width\s*=\s*['"]?1['"]?|height\s*=\s*['"]?1['"]?)[^>]*/?>"""
)
# Collapse whitespace in HTML text nodes is risky; only collapse runs of spaces
_RE_WS = re.compile(r"[ \t]{3,}")
_RE_BLANK_LINES = re.compile(r"\n{3,}")
_RE_TAGS_FOR_TEXT = re.compile(r"(?is)<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<[^>]+>")


def html_to_plain(html: str) -> str:
    """Cheap HTML → text (for preview / text fallback)."""
    if not html:
        return ""
    t = _RE_SCRIPT.sub(" ", html)
    t = _RE_STYLE.sub(" ", t)
    t = _RE_TAGS_FOR_TEXT.sub(" ", t)
    t = re.sub(r"&nbsp;", " ", t, flags=re.I)
    t = re.sub(r"&amp;", "&", t, flags=re.I)
    t = re.sub(r"&lt;", "<", t, flags=re.I)
    t = re.sub(r"&gt;", ">", t, flags=re.I)
    t = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x110000 else " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def slim_html(html: str | None, *, hard_max: int = HTML_HARD_MAX) -> str | None:
    """Strip bloat from HTML; return None if empty after clean."""
    if not html or not isinstance(html, str):
        return None
    s = html
    if not s.strip():
        return None

    # Always remove active / non-display blocks. Keep <style> so HTML mail
    # still lays out; data-URI images are the quota problem, not CSS.
    s = _RE_COMMENT.sub("", s)
    s = _RE_SCRIPT.sub("", s)
    s = _RE_NOSCRIPT.sub("", s)
    s = _RE_DATA_URI.sub(r'src="about:blank"', s)
    s = _RE_DATA_URI_CSS.sub("none", s)
    s = _RE_TRACKING_IMG.sub("", s)

    # Soft threshold: drop huge decorative SVG / class soup, keep <style>
    if len(s) > HTML_SOFT_BYTES:
        s = _RE_SVG.sub("", s)
        s = re.sub(
            r"""(?is)(src|href)\s*=\s*(['"])[^'"]{800,}\2""",
            r'\1="#"',
            s,
        )
        s = re.sub(r"""(?is)\s(?:class|id)\s*=\s*(['"]).*?\1""", "", s)
        s = _RE_WS.sub("  ", s)

    if len(s) > hard_max:
        # Prefer keeping head of message (codes usually near top)
        s = s[:hard_max] + "\n<!-- openmail:truncated -->"

    s = s.strip()
    return s or None


def slim_text(text: str | None, *, hard_max: int = TEXT_HARD_MAX) -> str | None:
    if not text or not isinstance(text, str):
        return None
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _RE_BLANK_LINES.sub("\n\n", t).strip()
    if len(t) > hard_max:
        t = t[:hard_max] + "…"
    return t or None


def slim_preview(preview: str | None, text: str | None, html: str | None) -> str | None:
    p = (preview or "").strip()
    if not p:
        p = (text or "").strip() or html_to_plain(html or "")
    p = re.sub(r"\s+", " ", p).strip()
    if not p:
        return None
    if len(p) > PREVIEW_MAX:
        p = p[: PREVIEW_MAX - 1] + "…"
    return p


def slim_message_fields(
    *,
    body_html: str | None = None,
    body_text: str | None = None,
    body_preview: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (body_html, body_text, body_preview) slimmed for client storage."""
    html = slim_html(body_html)
    text = slim_text(body_text)
    # If HTML was huge and text empty, derive plain from slimmed html
    if not text and html:
        text = slim_text(html_to_plain(html))
    preview = slim_preview(body_preview, text, html)
    return html, text, preview


def slim_message_obj(m: Any) -> Any:
    """Mutate a Message-like object in place and return it."""
    html = getattr(m, "body_html", None)
    text = getattr(m, "body_text", None)
    preview = getattr(m, "body_preview", None)
    h, t, p = slim_message_fields(body_html=html, body_text=text, body_preview=preview)
    try:
        m.body_html = h or ""
        m.body_text = t or ""
        m.body_preview = p or ""
    except Exception:
        pass
    return m


def estimated_message_chars(m: Any) -> int:
    """Rough payload size for diagnostics / tests."""
    parts = [
        getattr(m, "body_html", None) or "",
        getattr(m, "body_text", None) or "",
        getattr(m, "body_preview", None) or "",
        getattr(m, "subject", None) or "",
    ]
    return sum(len(str(x)) for x in parts)
