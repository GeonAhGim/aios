"""Foundation routers test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import to ensure module is loaded for coverage
import src.api.routers.foundation.connections  # noqa: F401
from src.services.auth_service import User


@pytest.fixture
def mock_user() -> User:
    """Fixture for authenticated user."""
    return User(
        user_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        email="test@example.com",
        display_name="Test User",
        mfa_enabled=True,
        mfa_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="active",
        is_verifier=True,
        is_platform_admin=False,
    )


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """TestClient for the test app."""
    return TestClient(test_app)
