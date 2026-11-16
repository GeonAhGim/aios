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
from src.api.routers.foundation.connections import router
from src.foundation.connections.domain.models import AccountConnection, ConnectionState
from src.foundation.connections.ports.provider import ReadonlyAccountProvider
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
def client(test_app: FastAPI) -> TestClient:
    """TestClient for the test app."""
    return TestClient(test_app)


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
def other_tenant_user(mock_user: User) -> User:
    """Fixture for a user with a different tenant_id (for cross-tenant tests)."""
    return User(
        user_id=UUID("00000000-0000-0000-0000-000000000002"),
        email="other@example.com",
        display_name="Other User",
        mfa_enabled=True,
        mfa_verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="active",
        is_verifier=True,
        is_platform_admin=False,
    )


@pytest.fixture
def mock_user_with_provider(mock_user: User) -> User:
    """Fixture for authenticated user with a configured provider in dependency overrides.

    The provider returns a valid scope proof so that confirm_connection can
    reach the tenant-check invariant (not fail on scope verification first).
    """
    from src.foundation.connections.domain.models import ScopeProof

    def get_mock_provider_with_scope() -> AsyncMock:
        provider = AsyncMock(spec=ReadonlyAccountProvider)
        provider.verify_readonly_scope.return_value = ScopeProof(
            granted_scopes=[],
            provider_verified=True,
        )
        return provider

    def get_mock_connection_repo() -> AsyncMock:
        repo = AsyncMock()
        repo.get_connection = AsyncMock(return_value=None)
        return repo

    from src.api import deps, foundation_deps

    # Override with the user-level fixtures already set up
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[deps.get_current_user] = lambda: mock_user
    app.dependency_overrides[foundation_deps.get_connection_repository] = get_mock_connection_repo
    app.dependency_overrides[foundation_deps.get_trust_repository] = lambda: AsyncMock()
    app.dependency_overrides[foundation_deps.get_readonly_account_provider] = (
        get_mock_provider_with_scope
    )
    app.dependency_overrides[foundation_deps.get_credential_encryption_key] = lambda: (
        "test-key-12345678901234567890"
    )
    return mock_user


@pytest.fixture
def cross_tenant_connection() -> AccountConnection:
    """A connection belonging to a different tenant than mock_user.

    tenant_id == 00000000-0000-0000-0000-000000000001 (different from
    mock_user.user_id == 550e8400-e29b-41d4-a716-446655440000).
    """
    return AccountConnection(
        id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        owner_subject_id=UUID("00000000-0000-0000-0000-000000000001"),
        provider_code="ex",
        opaque_account_ref="ref",
        state=ConnectionState.PENDING_CONSENT,
        capability_profile=(),
        revision=1,
    )


@pytest.fixture
def active_cross_tenant_connection() -> AccountConnection:
    """A connection in ACTIVE_READONLY state belonging to a different tenant."""
    return AccountConnection(
        id=UUID("550e8400-e29b-41d4-a716-446655440002"),
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        owner_subject_id=UUID("00000000-0000-0000-0000-000000000001"),
        provider_code="ex",
        opaque_account_ref="ref",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=(),
        revision=1,
    )
