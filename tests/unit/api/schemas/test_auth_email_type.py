"""Tests for the internal email type (ValidEmailType).

Covers four axes:
1. Valid — accepted emails pass through
2. Invalid — malformed emails are rejected with 422-equivalent error
3. Normalisation — domain is lowercased, local-part preserved
4. OpenAPI — schema emits type=string, format=email
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from src.api.schemas._email_type import ValidEmailType


class _DummyModel(BaseModel):
    """Thin wrapper so we can validate email values."""

    email: ValidEmailType


# ------------------------------------------------------------------ #
# 1. Valid emails — should pass through
# ------------------------------------------------------------------ #


class TestValidEmails:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("user@example.com", "user@example.com"),
            ("USER@EXAMPLE.COM", "USER@example.com"),
            ("user.name@domain.co.jp", "user.name@domain.co.jp"),
            ("user+tag@gmail.com", "user+tag@gmail.com"),
            ("user_123@test.org", "user_123@test.org"),
            ("a@b.cc", "a@b.cc"),
            ("u@x.org", "u@x.org"),
            # Hyphenated labels
            ("user@my-domain.com", "user@my-domain.com"),
            ("user@sub.domain.com", "user@sub.domain.com"),
            # Numeric TLD
            ("user@123.com", "user@123.com"),
        ],
    )
    def test_accepts_valid_email(self, raw: str, expected: str) -> None:
        obj = _DummyModel(email=raw)
        assert obj.email == expected

    def test_domain_lowercased(self) -> None:
        obj = _DummyModel(email="User@UPPER.COM")
        assert obj.email == "User@upper.com"

    def test_local_part_preserved(self) -> None:
        obj = _DummyModel(email="MixedCase@domain.com")
        assert obj.email == "MixedCase@domain.com"


# ------------------------------------------------------------------ #
# 2. Invalid emails — should raise ValidationError
# ------------------------------------------------------------------ #


class TestInvalidEmails:
    @pytest.mark.parametrize(
        "raw",
        [
            "not-an-email",  # no @
            "missing@",  # no domain
            "@domain.com",  # no local
            "",  # empty string
            "user@",  # bare @
            "@",  # just @
            "user name@domain.com",  # space in local
            "user@domain",  # no dot in domain
            "user@.com",  # domain starts with dot
            "user@domain.",  # domain ends with dot
            "user@-domain.com",  # domain label starts with hyphen
            "user@domain-.com",  # domain label ends with hyphen
            "user@domain.c",  # TLD too short (needs >= 2 alpha)
            "user@123",  # no TLD dot
            "user@.domain.com",  # label starts with dot
            "user name",  # space only
            "user\t@domain.com",  # tab
            "user\n@domain.com",  # newline
        ],
    )
    def test_rejects_invalid_email(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            _DummyModel(email=raw)

    def test_rejects_local_part_too_long(self) -> None:
        # 65 chars in local part (> 64 limit)
        long_local = "a" * 65
        with pytest.raises(ValidationError):
            _DummyModel(email=f"{long_local}@domain.com")

    def test_rejects_total_too_long(self) -> None:
        # Total > 254 chars: local=64, @=1, domain=190 -> total=255
        long_domain = "a" * 186 + ".com"  # 190 chars
        with pytest.raises(ValidationError):
            _DummyModel(email=f"{'a' * 64}@{long_domain}")


# ------------------------------------------------------------------ #
# 3. Normalisation
# ------------------------------------------------------------------ #


class TestNormalisation:
    def test_domain_always_lowercase(self) -> None:
        obj = _DummyModel(email="user@EXAMPLE.COM")
        assert obj.email == "user@example.com"

    def test_multiple_domain_labels_lowercased(self) -> None:
        obj = _DummyModel(email="user@SUB.Domain.COM")
        assert obj.email == "user@sub.domain.com"

    def test_local_case_preserved(self) -> None:
        obj = _DummyModel(email="MiXeD@domain.com")
        assert obj.email == "MiXeD@domain.com"


# ------------------------------------------------------------------ #
# 4. OpenAPI schema
# ------------------------------------------------------------------ #


class TestOpenAPISchema:
    def test_email_field_type_string(self) -> None:
        schema = _DummyModel.model_json_schema()
        email_schema = schema["properties"]["email"]
        assert email_schema["type"] == "string"

    def test_email_field_format_email(self) -> None:
        schema = _DummyModel.model_json_schema()
        email_schema = schema["properties"]["email"]
        assert email_schema["format"] == "email"

    def test_signup_request_schema_matches(self) -> None:
        """SignupRequest must emit type=string, format=email for email."""
        from src.api.schemas.auth import SignupRequest

        schema = SignupRequest.model_json_schema()
        email_schema = schema["properties"]["email"]
        assert email_schema["type"] == "string"
        assert email_schema["format"] == "email"

    def test_login_request_schema_matches(self) -> None:
        """LoginRequest must emit type=string, format=email for email."""
        from src.api.schemas.auth import LoginRequest

        schema = LoginRequest.model_json_schema()
        email_schema = schema["properties"]["email"]
        assert email_schema["type"] == "string"
        assert email_schema["format"] == "email"
