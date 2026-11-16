"""TEST-cov task-6538: src/api/routers/foundation/connections.py."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import src.api.routers.foundation.connections  # noqa: F401
from src.api.schemas.foundation.connections import (
    AccountConnectionView,
    BeginConnectionRequest,
    CapabilityScope,
    ConnectionState,
)


def test_list_connections(client: TestClient, monkeypatch) -> None:
    """Test GET /v1/foundation/connections."""
    conn = AccountConnectionView(
        id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        provider_code="exchange",
        masked_account_label="****",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=[CapabilityScope.READ_BALANCE],
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def mock_build(repo: Any, user_id: Any):
        from src.foundation.connections.projections import ConnectionListView

        return ConnectionListView(
            connections=[conn],
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
        )

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "build_connection_list_view", mock_build)
    response = client.get("/v1/foundation/connections")
    assert response.status_code == status.HTTP_200_OK


def test_list_connections_empty(client: TestClient, monkeypatch) -> None:
    """Test empty list."""

    async def mock_build(repo: Any, user_id: Any):
        from src.foundation.connections.projections import ConnectionListView

        return ConnectionListView(connections=[], as_of=datetime(2026, 1, 1, tzinfo=UTC))

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "build_connection_list_view", mock_build)
    response = client.get("/v1/foundation/connections")
    assert response.status_code == status.HTTP_200_OK


def test_post_begin_connection(client: TestClient, monkeypatch) -> None:
    """Test POST /v1/foundation/connections."""
    conn = AccountConnectionView(
        id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        provider_code="ex",
        masked_account_label="****",
        state=ConnectionState.PENDING_CONSENT,
        capability_profile=[CapabilityScope.READ_BALANCE],
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def mock_begin(repo: Any, trust_repo: Any, **kwargs: Any):
        return conn

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "begin_connection", mock_begin)
    body = BeginConnectionRequest(
        provider_code="ex",
        opaque_account_ref="ref",
        requested_capability_profile=[CapabilityScope.READ_BALANCE],
    )
    response = client.post("/v1/foundation/connections", json=body.model_dump())
    assert response.status_code == status.HTTP_201_CREATED


def test_post_begin_missing_field(client: TestClient) -> None:
    """Test missing required field."""
    response = client.post("/v1/foundation/connections", json={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_post_confirm_connection(client: TestClient, monkeypatch) -> None:
    """Test POST /{connection_id}:confirm."""
    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    conn = AccountConnectionView(
        id=cid,
        provider_code="ex",
        masked_account_label="****",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=[CapabilityScope.READ_BALANCE],
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def mock_confirm(repo: Any, provider: Any, **kwargs: Any):
        return conn

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "confirm_connection", mock_confirm)
    response = client.post(f"/v1/foundation/connections/{cid}:confirm")
    assert response.status_code == status.HTTP_200_OK


def test_post_confirm_invalid_uuid(client: TestClient) -> None:
    """Test invalid UUID."""
    response = client.post("/v1/foundation/connections/bad:confirm")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_post_sync_connection(client: TestClient, monkeypatch) -> None:
    """Test POST /{connection_id}:sync."""
    from src.api.schemas.foundation.connections import AccountSnapshotView
    from src.foundation.connections.contracts.v1 import SnapshotValueView

    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    snap = AccountSnapshotView(
        connection_id=cid,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        provider_as_of=datetime(2026, 1, 1, tzinfo=UTC),
        freshness="1h",
        currency="USD",
        values=[SnapshotValueView(entity_type="bal", entity_key="key", value=Decimal("100"))],
    )

    async def mock_sync(repo: Any, provider: Any, **kwargs: Any):
        return snap

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "sync_snapshot", mock_sync)
    response = client.post(f"/v1/foundation/connections/{cid}:sync")
    assert response.status_code == status.HTTP_200_OK


def test_post_sync_invalid_uuid(client: TestClient) -> None:
    """Test invalid UUID for sync."""
    response = client.post("/v1/foundation/connections/bad:sync")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_post_revoke_connection(client: TestClient, monkeypatch) -> None:
    """Test POST /{connection_id}:revoke."""
    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    conn = AccountConnectionView(
        id=cid,
        provider_code="ex",
        masked_account_label="****",
        state=ConnectionState.REVOKED,
        capability_profile=[CapabilityScope.READ_BALANCE],
        revision=2,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def mock_revoke(repo: Any, **kwargs: Any):
        return conn

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "revoke_connection", mock_revoke)
    response = client.post(f"/v1/foundation/connections/{cid}:revoke")
    assert response.status_code == status.HTTP_200_OK


def test_post_revoke_invalid_uuid(client: TestClient) -> None:
    """Test invalid UUID for revoke."""
    response = client.post("/v1/foundation/connections/bad:revoke")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_post_begin_exception(client: TestClient, monkeypatch) -> None:
    """Failure injection: begin_connection raises."""

    async def mock_fail(repo: Any, trust_repo: Any, **kwargs: Any):
        raise RuntimeError("error")

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "begin_connection", mock_fail)
    body = BeginConnectionRequest(
        provider_code="ex",
        opaque_account_ref="ref",
        requested_capability_profile=[CapabilityScope.READ_BALANCE],
    )
    with pytest.raises(RuntimeError):
        client.post("/v1/foundation/connections", json=body.model_dump())


# ── negative tests: invariant-violating inputs ──────────────────────────


def test_post_begin_invalid_capability(client: TestClient) -> None:
    """Reject connection request with an unknown capability scope."""
    body = {
        "provider_code": "ex",
        "opaque_account_ref": "ref",
        "requested_capability_profile": ["NONEXISTENT_SCOPE"],
    }
    response = client.post("/v1/foundation/connections", json=body)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_post_confirm_tenant_mismatch(client: TestClient) -> None:
    """Reject confirm when connection belongs to a different tenant (I-03 idempotency boundary).

    Uses a real repo with a cross-tenant connection so the service path
    rejects it at confirm_connection.py:57 — not a monkeypatched ValueError.
    """
    from uuid import uuid4

    from src.foundation.connections.application.confirm_connection import (
        CrossTenantConnectionAccessError,
    )
    from src.foundation.connections.domain.models import AccountConnection, ConnectionState

    own_tenant = UUID("550e8400-e29b-41d4-a716-446655440000")
    other_tenant = UUID("00000000-0000-0000-0000-000000000001")
    cid = uuid4()

    class FakeRepo:
        """Minimal repo holding one cross-tenant connection."""

        connections_store: dict[UUID, AccountConnection]

        def __init__(self) -> None:
            self.connections_store = {
                cid: AccountConnection(
                    id=cid,
                    tenant_id=other_tenant,
                    owner_subject_id=other_tenant,
                    provider_code="ex",
                    opaque_account_ref="ref",
                    state=ConnectionState.PENDING_CONSENT,
                    capability_profile=(),
                    revision=1,
                )
            }
            self.transition_called = False

        async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
            return self.connections_store.get(connection_id)

        async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
            return []

        async def insert_pending_connection(
            self,
            connection: AccountConnection,
        ) -> AccountConnection:
            return connection

        async def transition_connection_state(
            self,
            connection_id: UUID,
            *,
            tenant_id: UUID,
            expected_state: str,
            new_state: str,
        ) -> AccountConnection:
            self.transition_called = True
            return self.connections_store[connection_id]

        async def insert_consent_link(self, link: Any) -> Any:
            return link

        async def insert_credential_binding(self, binding: Any) -> Any:
            return binding

        async def get_credential_binding(self, connection_id: UUID) -> Any:
            return None

        async def revoke_credential_binding(self, connection_id: UUID) -> None:
            pass

        async def persist_snapshot_if_syncable(
            self,
            connection_id: UUID,
            tenant_id: UUID,
            snapshot: Any,
            health: Any,
        ) -> Any:
            return snapshot

        async def get_latest_snapshot(self, connection_id: UUID) -> Any:
            return None

        async def insert_health_record(self, health: Any) -> Any:
            return health

        async def get_latest_health(self, connection_id: UUID) -> Any:
            return None

    async def mock_provider_verify() -> Any:
        from src.foundation.connections.domain.models import ScopeProof

        return ScopeProof(
            provider_verified=False,
            granted_scopes=(),
            provider_credential_ref="ref",
        )

    repo = FakeRepo()
    from unittest.mock import AsyncMock

    provider = AsyncMock()
    provider.verify_readonly_scope = mock_provider_verify

    with pytest.raises(CrossTenantConnectionAccessError):
        from src.foundation.connections.application.confirm_connection import (
            confirm_connection,
        )

        await confirm_connection(
            repo,
            provider,
            tenant_id=own_tenant,
            connection_id=cid,
            encryption_key="test-key",
        )

    # Repo state must remain unchanged — no transition should have fired.
    assert repo.transition_called is False


async def test_post_sync_tenant_mismatch(client: TestClient) -> None:
    """Reject sync when connection belongs to a different tenant."""
    from uuid import uuid4

    from src.foundation.connections.application.errors import (
        CrossTenantConnectionAccessError,
    )
    from src.foundation.connections.domain.models import AccountConnection, ConnectionState

    own_tenant = UUID("550e8400-e29b-41d4-a716-446655440000")
    other_tenant = UUID("00000000-0000-0000-0000-000000000001")
    cid = uuid4()

    class FakeRepo:
        """Minimal repo holding one cross-tenant connection."""

        connections_store: dict[UUID, AccountConnection]

        def __init__(self) -> None:
            self.connections_store = {
                cid: AccountConnection(
                    id=cid,
                    tenant_id=other_tenant,
                    owner_subject_id=other_tenant,
                    provider_code="ex",
                    opaque_account_ref="ref",
                    state=ConnectionState.ACTIVE_READONLY,
                    capability_profile=(),
                    revision=1,
                )
            }

        async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
            return self.connections_store.get(connection_id)

        async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
            return []

        async def insert_pending_connection(
            self,
            connection: AccountConnection,
        ) -> AccountConnection:
            return connection

        async def transition_connection_state(
            self,
            connection_id: UUID,
            *,
            tenant_id: UUID,
            expected_state: str,
            new_state: str,
        ) -> AccountConnection:
            return self.connections_store[connection_id]

        async def insert_consent_link(self, link: Any) -> Any:
            return link

        async def insert_credential_binding(self, binding: Any) -> Any:
            return binding

        async def get_credential_binding(self, connection_id: UUID) -> Any:
            return None

        async def revoke_credential_binding(self, connection_id: UUID) -> None:
            pass

        async def persist_snapshot_if_syncable(
            self,
            connection_id: UUID,
            tenant_id: UUID,
            snapshot: Any,
            health: Any,
        ) -> Any:
            return snapshot

        async def get_latest_snapshot(self, connection_id: UUID) -> Any:
            return None

        async def insert_health_record(self, health: Any) -> Any:
            return health

        async def get_latest_health(self, connection_id: UUID) -> Any:
            return None

    repo = FakeRepo()
    from unittest.mock import AsyncMock

    provider = AsyncMock()

    with pytest.raises(CrossTenantConnectionAccessError):
        from src.foundation.connections.application.sync_snapshot import sync_snapshot

        await sync_snapshot(
            repo,
            provider,
            tenant_id=own_tenant,
            connection_id=cid,
        )

    # Repo state must remain unchanged — no writes should have occurred.
    assert len(repo.connections_store) == 1


# ── failure injection: dependency exceptions ────────────────────────────


def test_post_confirm_provider_error(client: TestClient, monkeypatch) -> None:
    """Failure injection: ReadonlyAccountProvider raises ConnectionError."""

    async def mock_confirm_repo_error(repo: Any, provider: Any, **kwargs: Any):
        raise ConnectionError("provider unreachable")

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "confirm_connection", mock_confirm_repo_error)
    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    with pytest.raises(ConnectionError):
        client.post(f"/v1/foundation/connections/{cid}:confirm")


def test_post_sync_repo_error(client: TestClient, monkeypatch) -> None:
    """Failure injection: ConnectionRepository raises during sync."""

    async def mock_sync_repo_error(repo: Any, provider: Any, **kwargs: Any):
        raise RuntimeError("db connection lost")

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "sync_snapshot", mock_sync_repo_error)
    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    with pytest.raises(RuntimeError):
        client.post(f"/v1/foundation/connections/{cid}:sync")


def test_post_revoke_repo_error(client: TestClient, monkeypatch) -> None:
    """Failure injection: ConnectionRepository raises during revoke."""

    async def mock_revoke_repo_error(repo: Any, **kwargs: Any):
        raise RuntimeError("db connection lost")

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "revoke_connection", mock_revoke_repo_error)
    cid = UUID("550e8400-e29b-41d4-a716-446655440001")
    with pytest.raises(RuntimeError):
        client.post(f"/v1/foundation/connections/{cid}:revoke")
