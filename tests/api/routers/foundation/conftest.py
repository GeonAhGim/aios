"""Foundation routers test fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import to ensure module is loaded for coverage
import src.api.routers.foundation.connections  # noqa: F401
from src.api.contracts.handlers import install_exception_handlers
from src.api.routers.foundation.connections import router
from src.foundation.connections.domain.models import AccountConnection, ConnectionState
from src.services.auth_service import User


@pytest.fixture
def mock_user() -> User:
    """Fixture for authenticated user."""
    return User(
        user_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        email="test@example.com",
        display_name="Test User",
        mfa_enabled=True,
        mfa_verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="active",
        is_verifier=True,
        is_platform_admin=False,
    )


@pytest.fixture
def connection_repo() -> AsyncMock:
    """Single repository mock shared by the app override and the test body.

    Tests can configure `get_connection` and assert that no write method was hit.
    """
    return AsyncMock()


@pytest.fixture
def other_tenant_connection() -> AccountConnection:
    """Connection owned by a tenant different from `mock_user` (cross-tenant probe)."""
    other = UUID("00000000-0000-0000-0000-000000000002")
    return AccountConnection(
        id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        tenant_id=other,
        owner_subject_id=other,
        provider_code="ex",
        opaque_account_ref="ref",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=(),
        revision=1,
    )


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """TestClient for the test app (unhandled exceptions propagate)."""
    return TestClient(test_app)


@pytest.fixture
def enveloped_client(test_app: FastAPI) -> TestClient:
    """TestClient that exercises the real exception-handler chain (HTTP envelopes)."""
    install_exception_handlers(test_app)
    return TestClient(test_app, raise_server_exceptions=False)


@pytest.fixture
def test_app(mock_user: User, connection_repo: AsyncMock) -> FastAPI:
    """Create a test FastAPI application with mocked dependencies."""
    app = FastAPI()
    app.include_router(router)

    def get_mock_user() -> User:
        return mock_user

    def get_mock_connection_repo() -> AsyncMock:
        return connection_repo

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
