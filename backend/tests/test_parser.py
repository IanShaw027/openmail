"""Tests for verification code parser."""

from __future__ import annotations

from app.providers.base import Message
from app.services.parser import annotate_message_code, extract_verification_code


def test_extract_near_chinese_keyword() -> None:
    code = extract_verification_code(
        subject="您的验证码是 482913，请在5分钟内使用",
        body_text="",
    )
    assert code == "482913"


def test_extract_near_english_code_keyword() -> None:
    code = extract_verification_code(
        subject="Your verification code",
        body_text="Use code 918273 to sign in.",
    )
    assert code == "918273"


def test_extract_otp_keyword() -> None:
    code = extract_verification_code(
        body_text="Your OTP is 445566 for login.",
    )
    assert code == "445566"


def test_bare_six_digit() -> None:
    code = extract_verification_code(subject="Login notice 112233 expires soon")
    assert code == "112233"


def test_prefer_keyword_over_random_body_number() -> None:
    code = extract_verification_code(
        subject="Security alert",
        body_text="Order #99887766 shipped. Your verification code is 556677.",
    )
    assert code == "556677"


def test_custom_regex() -> None:
    code = extract_verification_code(
        body_text="PIN: AB-7788-CD",
        custom_regex=r"PIN:\s*AB-(\d{4})-CD",
    )
    assert code == "7788"


def test_no_code() -> None:
    assert extract_verification_code(subject="Hello", body_text="No digits here") is None


def test_html_body_stripped() -> None:
    code = extract_verification_code(
        body_html="<p>Your code is <b>334455</b></p>",
    )
    assert code == "334455"


def test_annotate_message() -> None:
    msg = Message(id="1", subject="验证码 667788")
    assert annotate_message_code(msg) == "667788"
    assert msg.verification_code == "667788"


def test_four_to_eight_digits() -> None:
    assert extract_verification_code(subject="code 1234 ready") == "1234"
    assert extract_verification_code(subject="code 12345678 ready") == "12345678"


def test_alphanumeric_confirmation_code_spacexai() -> None:
    """SpaceXAI / similar: confirmation code: 8IX-FGG (not pure digits)."""
    assert (
        extract_verification_code(subject="SpaceXAI confirmation code: 8IX-FGG")
        == "8IX-FGG"
    )
    assert (
        extract_verification_code(
            subject="Your SpaceXAI login",
            body_text="SpaceXAI confirmation code: 8IX-FGG\nExpires in 10 minutes.",
        )
        == "8IX-FGG"
    )


def test_alphanumeric_code_keyword() -> None:
    assert extract_verification_code(body_text="Your access code is AB12-CD34.") == "AB12-CD34"
    assert extract_verification_code(subject="OTP", body_text="code: X9Y8Z7") == "X9Y8Z7"
    assert (
        extract_verification_code(subject="Your confirmation code is 8IX-FGG") == "8IX-FGG"
    )


def test_alphanumeric_not_random_words() -> None:
    # Should not pick plain English near "code" without digit/hyphen signal
    assert (
        extract_verification_code(
            subject="Please confirm",
            body_text="Click the link below to confirm your account.",
        )
        is None
    )
