"""TEST-cov task-6538: src/api/routers/foundation/connections.py."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def mock_build(repo, user_id):  # type: ignore
        from src.foundation.connections.projections import ConnectionListView

        return ConnectionListView(
            connections=[conn],
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    from src.api.routers.foundation import connections

    monkeypatch.setattr(connections, "build_connection_list_view", mock_build)
    response = client.get("/v1/foundation/connections")
    assert response.status_code == status.HTTP_200_OK


def test_list_connections_empty(client: TestClient, monkeypatch) -> None:
    """Test empty list."""

    async def mock_build(repo, user_id):  # type: ignore
        from src.foundation.connections.projections import ConnectionListView

        return ConnectionListView(connections=[], as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))

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
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def mock_begin(repo, trust_repo, **kwargs):  # type: ignore
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
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def mock_confirm(repo, provider, **kwargs):  # type: ignore
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
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        provider_as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        freshness="1h",
        currency="USD",
        values=[SnapshotValueView(entity_type="bal", entity_key="key", value=Decimal("100"))],
    )

    async def mock_sync(repo, provider, **kwargs):  # type: ignore
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
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def mock_revoke(repo, **kwargs):  # type: ignore
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

    async def mock_fail(repo, trust_repo, **kwargs):  # type: ignore
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
