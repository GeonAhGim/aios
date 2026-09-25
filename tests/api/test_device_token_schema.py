"""Tests for src/api/schemas/device_token.py — DeviceTokenRegisterRequest schema coverage.

Covers statement execution paths, boundary validation, and error handling
for the DeviceTokenRegisterRequest Pydantic model.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.device_token import DeviceTokenRegisterRequest


class TestDeviceTokenRegisterRequestValid:
    """Happy-path construction — confirms fields are populated correctly."""

    def test_minimal_valid(self):
        """Basic valid payload with minimum-length token and platform."""
        schema = DeviceTokenRegisterRequest(device_token="abc", platform="ios")
        assert schema.device_token == "abc"
        assert schema.platform == "ios"

    def test_long_token(self):
        """Device token at typical maximum length (256 chars)."""
        long_token = "a" * 256
        schema = DeviceTokenRegisterRequest(device_token=long_token, platform="android")
        assert schema.device_token == long_token

    def test_model_dump(self):
        """Serialized output matches input fields."""
        schema = DeviceTokenRegisterRequest(device_token="tok123", platform="web")
        dump = schema.model_dump()
        assert dump == {"device_token": "tok123", "platform": "web"}

    def test_model_dump_json(self):
        """JSON serialization produces valid JSON string."""
        schema = DeviceTokenRegisterRequest(device_token="tok456", platform="ios")
        json_str = schema.model_dump_json()
        assert '"device_token":"tok456"' in json_str
        assert '"platform":"ios"' in json_str


class TestDeviceTokenRegisterRequestNegative:
    """Negative tests — invalid inputs that must raise ValidationError."""

    def test_empty_device_token_accepted(self):
        """Pydantic v2 Strict str accepts empty string — validation is structural only."""
        schema = DeviceTokenRegisterRequest(device_token="", platform="ios")
        assert schema.device_token == ""

    def test_whitespace_only_device_token_accepted(self):
        """Whitespace-only device_token is accepted by the schema (backend enforces format)."""
        schema = DeviceTokenRegisterRequest(device_token="   ", platform="android")
        assert schema.device_token == "   "

    def test_missing_device_token_field(self):
        """Omitting required device_token field raises error."""
        with pytest.raises(ValidationError) as exc_info:
            DeviceTokenRegisterRequest(platform="ios")
        assert "device_token" in str(exc_info.value)

    def test_missing_platform_field(self):
        """Omitting required platform field raises error."""
        with pytest.raises(ValidationError) as exc_info:
            DeviceTokenRegisterRequest(device_token="abc")
        assert "platform" in str(exc_info.value)

    def test_both_fields_missing(self):
        """Omitting both required fields raises error listing both."""
        with pytest.raises(ValidationError) as exc_info:
            DeviceTokenRegisterRequest()
        error_str = str(exc_info.value)
        assert "device_token" in error_str
        assert "platform" in error_str

    def test_none_device_token(self):
        """Passing None for device_token should raise ValidationError."""
        with pytest.raises(ValidationError):
            DeviceTokenRegisterRequest(device_token=None, platform="ios")

    def test_none_platform(self):
        """Passing None for platform should raise ValidationError."""
        with pytest.raises(ValidationError):
            DeviceTokenRegisterRequest(device_token="abc", platform=None)


class TestDeviceTokenRegisterRequestEdgeCases:
    """Edge cases and type coercion behavior."""

    def test_numeric_token_rejected_strict(self):
        """Pydantic v2 Strict str rejects int — no coercion."""
        with pytest.raises(ValidationError):
            DeviceTokenRegisterRequest(device_token=123, platform="ios")

    def test_special_chars_in_token(self):
        """Device token with special characters is accepted (backend validates format)."""
        special_token = "abc!@#$%^&*()_+-=[]{}|;':\",./<>?"
        schema = DeviceTokenRegisterRequest(device_token=special_token, platform="web")
        assert schema.device_token == special_token

    def test_uppercase_platform(self):
        """Uppercase platform values are accepted as-is."""
        schema = DeviceTokenRegisterRequest(device_token="abc", platform="IOS")
        assert schema.platform == "IOS"

    def test_empty_platform_accepted(self):
        """Pydantic v2 Strict str accepts empty string for platform."""
        schema = DeviceTokenRegisterRequest(device_token="abc", platform="")
        assert schema.platform == ""
