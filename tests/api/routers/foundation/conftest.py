"""Foundation routers test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import to ensure module is loaded for coverage
import src.api.routers.foundation.connections  # noqa: F401
from src.api.routers.foundation.connections import router
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
def test_app(mock_user: User) -> FastAPI:
    """Create a test FastAPI application with mocked dependencies."""
    app = FastAPI()
    app.include_router(router)

    def get_mock_user() -> User:
        return mock_user

    def get_mock_connection_repo() -> AsyncMock:
        return AsyncMock()

    def get_mock_trust_repo() -> AsyncMock:
        return AsyncMock()

    def get_mock_provider() -> AsyncMock:
        return AsyncMock()

    def get_mock_encryption_key() -> str:
        return "test-key-12345678901234567890"

    from src.api import deps, foundation_deps

    app.dependency_overrides[deps.get_current_user] = get_mock_user
    app.dependency_overrides[foundation_deps.get_connection_repository] = get_mock_connection_repo
    app.dependency_overrides[foundation_deps.get_trust_repository] = get_mock_trust_repo
    app.dependency_overrides[foundation_deps.get_readonly_account_provider] = get_mock_provider
    app.dependency_overrides[foundation_deps.get_credential_encryption_key] = (
        get_mock_encryption_key
    )

    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """TestClient for the test app."""
    return TestClient(test_app)
