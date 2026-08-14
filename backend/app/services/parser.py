"""Verification-code extraction shared by all mail providers.

Priority:
1. Optional custom regex
2. Subject: keyword-adjacent digits / alphanumeric (e.g. 8IX-FGG)
3. Body near 验证码 / code / OTP / confirmation code keywords
4. Bare 4–8 digit runs in subject
5. Short-body 6-digit fallback
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Pure digit OTP (classic)
_DIGIT_RUN = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
# Alphanumeric tokens: AB12CD, 8IX-FGG, A1B2-C3D4 (must contain a letter)
_ALNUM_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z0-9]{3,8}(?:-[A-Za-z0-9]{2,8}){0,3})"
    r"(?![A-Za-z0-9])"
)

# Keywords that introduce a verification code. Avoid bare "one-time" (matches
# "one-time purchase") and prefer compound phrases over lone "code" where possible.
# Lone "pin" is too broad (spin / PIN-less notices); require "pin code".
_KW = (
    r"验证码|校验码|动态码|确认码|临时验证码|"
    r"confirmation\s*code|verification\s*code|security\s*code|"
    r"access\s*code|login\s*code|auth(?:entication)?\s*code|"
    r"temporary\s+(?:login\s+|verification\s+|sign[-\s]?in\s+)?code|"
    r"one[-\s]?time\s+(?:pass(?:word|code)|code|otp|pin)|"
    r"\botp\b|pin\s*code|"
    # Lone "code" still needed for "Your code is 123456" but only as a word
    r"(?<![A-Za-z])code(?![A-Za-z])"
)

# 4–5 digit OTPs only next to these; weak lone "code" needs 6–8 digits.
_STRONG_DIGIT_KW = re.compile(
    r"验证码|校验码|动态码|确认码|临时验证码|"
    r"confirmation\s*code|verification\s*code|security\s*code|"
    r"access\s*code|login\s*code|auth(?:entication)?\s*code|"
    r"one[-\s]?time\s+(?:pass(?:word|code)|code|otp|pin)|"
    r"\botp\b|passcode|pin\s*code|your\s+\w+\s+code",
    re.IGNORECASE,
)

# "postal code" / "error code" / "promo code" are not OTPs.
_FALSE_CODE_PHRASE = re.compile(
    r"(?:postal|zip|error|status|promo|discount|area|country|source|"
    r"html|css|coupon|gift|tracking|order|sku|invoice|item|model|"
    r"version|http|https|preferences|conduct|review|batch|response|"
    r"exit|return|http\s*status)\s*[-_]?\s*codes?",
    re.IGNORECASE,
)

_SUBJECT_NEAR = re.compile(
    rf"(?:{_KW})[^\w]{{0,24}}(\d{{4,8}})"
    rf"|(\d{{4,8}})[^\w]{{0,16}}(?:验证码|校验码|code|otp)",
    re.IGNORECASE,
)
_BODY_NEAR = re.compile(
    rf"(?:{_KW})"
    rf"[^\d]{{0,48}}(\d{{4,8}})"
    rf"|(\d{{4,8}})[^\d]{{0,24}}(?:验证码|校验码|is\s+your\s+code)",
    re.IGNORECASE | re.DOTALL,
)
# Keyword then alphanumeric (SpaceXAI: "confirmation code: 8IX-FGG" / "code is 8IX-FGG")
_ALNUM_NEAR = re.compile(
    rf"(?:{_KW})"
    rf"(?:[\s:：#=\-–—]|is|为|：|是){{0,24}}"
    rf"([A-Za-z0-9]{{3,8}}(?:-[A-Za-z0-9]{{2,8}}){{0,3}})"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Reject tokens that look like years / plain words, not codes
_ALNUM_STOP = frozenset(
    {
        "code",
        "codes",
        "http",
        "https",
        "www",
        "mail",
        "email",
        "from",
        "your",
        "with",
        "this",
        "that",
        "login",
        "signin",
        "signup",
        "account",
        "please",
        "click",
        "here",
        "link",
        "token",
        "reset",
        "space",
        "spacexai",
        "gmail",
        "outlook",
        "microsoft",
        "google",
        "apple",
        "confirm",
        "confirmation",
        "verify",
        "verification",
        "security",
        "access",
        "password",
        "passcode",
        "onetime",
        "minute",
        "minutes",
        "hour",
        "hours",
        "expire",
        "expires",
        "valid",
        "using",
        "enter",
        "below",
        "above",
        "subject",
    }
)


def _strip_html(html: str) -> str:
    text = _HTML_TAG.sub(" ", html or "")
    return _WS.sub(" ", text).strip()


def _normalize_code(raw: str | None) -> str | None:
    if not raw:
        return None
    code = raw.strip().strip(".,;:\"'()[]{}")
    if not code:
        return None
    return code


def _is_plausible_digit_code(token: str) -> bool:
    """Reject years / obvious non-OTP pure digit runs."""
    t = (token or "").strip()
    if not re.fullmatch(r"\d{4,8}", t):
        return False
    # Calendar years commonly appear in marketing / legal / © footers
    if re.fullmatch(r"(?:19|20)\d{2}", t):
        return False
    # All same digit (000000) — weak signal, rarely a real OTP in our corpus
    if len(set(t)) == 1:
        return False
    return True


def _is_plausible_alnum(token: str) -> bool:
    """Accept 8IX-FGG / AB12CD / M1M-J00; reject pure words like purchase / two-factor."""
    t = token.strip()
    if len(t) < 4 or len(t) > 24:
        return False
    compact = t.replace("-", "")
    if not re.fullmatch(r"[A-Za-z0-9]+", compact):
        return False
    # Must include at least one letter (digit-only is digit path)
    if not re.search(r"[A-Za-z]", compact):
        return False
    # Real product codes almost always mix a digit in (8IX-FGG, M1M-J00, X9Y8Z7).
    # Pure letter tokens ("purchase", "two-factor", "anti-spam") are English noise.
    if not re.search(r"\d", compact):
        return False
    if compact.lower() in _ALNUM_STOP:
        return False
    if t.lower() in _ALNUM_STOP:
        return False
    # Reject multi-hyphen English phrases (choose-your-country)
    if t.count("-") >= 2 and not re.search(r"\d", t):
        return False
    return True


def _first_group(m: re.Match[str]) -> str | None:
    for g in m.groups():
        if g:
            return _normalize_code(g)
    return None


def _find_alnum_near(blob: str) -> str | None:
    if not blob:
        return None
    for m in _ALNUM_NEAR.finditer(blob):
        cand = _normalize_code(m.group(1))
        if cand and _is_plausible_alnum(cand):
            return cand
    return None


def _scrub_false_code_phrases(text: str) -> str:
    if not text:
        return text
    return _FALSE_CODE_PHRASE.sub(" ", text)


def _accept_digit_in_context(token: str | None, blob: str, match: re.Match[str]) -> str | None:
    """6–8 digits may sit next to weak 'code'; 4–5 need a strong OTP keyword."""
    if not token or not _is_plausible_digit_code(token):
        return None
    if len(token) >= 6:
        return token
    start = max(0, match.start() - 40)
    end = min(len(blob), match.end() + 16)
    if _STRONG_DIGIT_KW.search(blob[start:end]):
        return token
    return None


def _digit_from_match(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    g = _first_group(m) if m.lastindex else _normalize_code(m.group(0))
    if g and _is_plausible_digit_code(g):
        return g
    # single-group digit patterns
    if m.lastindex is None:
        g2 = _normalize_code(m.group(1) if m.groups() else m.group(0))
        if g2 and _is_plausible_digit_code(g2):
            return g2
    for g in m.groups() or ():
        if not g:
            continue
        g = _normalize_code(g)
        if g and _is_plausible_digit_code(g):
            return g
    return None


def extract_verification_code(
    *,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    body_preview: str | None = None,
    custom_regex: str | None = None,
) -> str | None:
    """Return best-effort verification code or None."""
    subject = subject or ""
    body_text = body_text or ""
    body_preview = body_preview or ""
    if not body_text and body_html:
        body_text = _strip_html(body_html)
    body_blob = "\n".join(x for x in (body_text, body_preview) if x)

    if custom_regex:
        try:
            cre = re.compile(custom_regex, re.IGNORECASE | re.DOTALL)
        except re.error:
            cre = None
        if cre is not None:
            for blob in (subject, body_blob, body_html or ""):
                m = cre.search(blob)
                if not m:
                    continue
                if m.lastindex:
                    for i in range(1, m.lastindex + 1):
                        g = (m.group(i) or "").strip()
                        if not g:
                            continue
                        # custom captures: keep short digits; filter year-like 4-digit
                        if re.fullmatch(r"\d{3,12}", g):
                            if len(g) < 4 or _is_plausible_digit_code(g):
                                return g
                            continue
                        if _is_plausible_alnum(g):
                            return g
                whole = _normalize_code(m.group(0))
                if whole and _is_plausible_alnum(whole):
                    return whole
                digits = re.search(r"\d{4,8}", m.group(0))
                if digits and _is_plausible_digit_code(digits.group(0)):
                    return digits.group(0)

    subject = _scrub_false_code_phrases(subject)
    body_blob = _scrub_false_code_phrases(body_blob)

    # 1) Subject: keyword-adjacent digits
    m = _SUBJECT_NEAR.search(subject)
    if m:
        g = _accept_digit_in_context(_digit_from_match(m), subject, m)
        if g:
            return g
    # 1b) Subject alphanumeric near confirmation/code keywords
    alnum = _find_alnum_near(subject)
    if alnum:
        return alnum
    # Bare digits in subject ONLY when subject also looks code-related.
    # "login" / "sign-in" alone are too weak (login notices, IP alerts).
    subj_low = subject.lower()
    if any(k in subj_low for k in ("code", "otp", "验证", "校验", "passcode")) or re.search(
        r"\bpin\b", subj_low
    ):
        m = _DIGIT_RUN.search(subject)
        if m and _is_plausible_digit_code(m.group(1)):
            g = _accept_digit_in_context(m.group(1), subject, m)
            if g:
                return g

    # 2) Body near keywords (digits)
    m = _BODY_NEAR.search(body_blob)
    if m:
        g = _accept_digit_in_context(_digit_from_match(m), body_blob, m)
        if g:
            return g
    # 2b) Body alphanumeric (SpaceXAI etc.)
    alnum = _find_alnum_near(body_blob)
    if alnum:
        return alnum

    # HTML-only body
    if body_html and not body_blob:
        stripped = _scrub_false_code_phrases(_strip_html(body_html))
        m = _BODY_NEAR.search(stripped)
        if m:
            g = _accept_digit_in_context(_digit_from_match(m), stripped, m)
            if g:
                return g
        alnum = _find_alnum_near(stripped)
        if alnum:
            return alnum
        m = _DIGIT_RUN.search(stripped)
        if m and _accept_digit_in_context(m.group(1), stripped, m):
            low = stripped.lower()
            if any(
                k in low
                for k in (
                    "otp",
                    "验证",
                    "校验",
                    "verification code",
                    "login code",
                    "passcode",
                    "confirmation code",
                )
            ):
                return m.group(1)

    # Fallback: first 6-digit run in short body that looks code-like.
    # Keyword may live in the subject (Stripe) while digits are only in the body.
    if body_blob and len(body_blob) < 1200:
        low = f"{subject}\n{body_blob}".lower()
        # Bare "code" / "signin" match too much marketing and status mail
        if any(
            k in low
            for k in (
                "otp",
                "验证",
                "校验",
                "verification code",
                "login code",
                "passcode",
                "one-time",
                "onetime",
                "confirmation code",
            )
        ):
            m6 = re.search(r"(?<!\d)(\d{6})(?!\d)", body_blob)
            if m6 and _is_plausible_digit_code(m6.group(1)):
                return m6.group(1)

    # Hyphenated product OTPs (M1M-J00) may sit before the keyword.
    # False phrases are already scrubbed, so leftover "code" is meaningful.
    for blob in (subject, body_blob):
        if not blob or len(blob) >= 1200:
            continue
        low = blob.lower()
        if not any(k in low for k in ("code", "otp", "验证", "校验", "passcode")):
            continue
        for m in _ALNUM_TOKEN.finditer(blob):
            cand = _normalize_code(m.group(1))
            if cand and _is_plausible_alnum(cand) and "-" in cand:
                return cand

    return None


# Aliases used by providers
def extract_code(
    *,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    body_preview: str | None = None,
    custom_regex: str | None = None,
) -> str | None:
    return extract_verification_code(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        body_preview=body_preview,
        custom_regex=custom_regex,
    )


def annotate_message_code(
    msg: Any,
    *,
    custom_regex: str | None = None,
) -> str | None:
    """Set msg.verification_code if missing; return the code (or existing)."""
    current = getattr(msg, "verification_code", None)
    if current:
        return current
    code = extract_verification_code(
        subject=getattr(msg, "subject", None) or "",
        body_text=getattr(msg, "body_text", None) or "",
        body_html=getattr(msg, "body_html", None) or "",
        body_preview=getattr(msg, "body_preview", None) or "",
        custom_regex=custom_regex,
    )
    try:
        msg.verification_code = code
    except Exception:
        if isinstance(msg, dict):
            msg["verification_code"] = code
    return code


def attach_verification_code(
    messages: Iterable[Any],
    *,
    custom_regex: str | None = None,
) -> list[Any]:
    """Mutate Message-like objects: set verification_code when missing."""
    out: list[Any] = []
    for msg in messages:
        annotate_message_code(msg, custom_regex=custom_regex)
        out.append(msg)
    return out
