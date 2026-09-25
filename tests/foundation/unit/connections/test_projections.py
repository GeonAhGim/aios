"""FND-05 `projections.py` -- mocked failure injection + perf assertion.

TEST-cov(task-4675): `build_connection_list_view()` fans out one
`list_connections()` call into N `get_credential_binding()` calls (one per
connection) and assembles the result into a `ConnectionListView` carrying
`as_of` (module docstring #71 §4 "read model may lag" / #108 §2 "as_of is
always included"). None of that fan-out/aggregation behaviour was covered
anywhere -- this file isolates it behind an in-memory fake repository so
repository failures on either call can be forced on demand.

Connections is not itself a safety/execution/ledger/compliance/data axis
(ADR-2026-09-09-C axis list, same reasoning as
`test_begin_connection_application.py`), so only the D2 floor applies here.

# ratchet-allow: fail-closed Protocol stub methods (FakeConnectionRepository
# members build_connection_list_view() never calls) raise NotImplementedError
# so an accidental call surfaces immediately instead of returning a silently
# wrong fake value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    ConnectionConsent,
    ConnectionHealth,
    ConnectionState,
    CredentialBinding,
    CredentialClass,
)
from src.foundation.connections.projections import (
    ConnectionListView,
    build_connection_list_view,
)

_NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def _connection(tenant_id: UUID, *, provider_code: str = "BITGET") -> AccountConnection:
    return AccountConnection(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        provider_code=provider_code,
        opaque_account_ref="ACCT1234567890",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=(),
        revision=1,
        created_at=_NOW,
    )


def _binding(connection_id: UUID, *, scope_verified: bool = True) -> CredentialBinding:
    return CredentialBinding(
        id=uuid4(),
        connection_id=connection_id,
        vault_secret_ref="vault://ref",
        scope_fingerprint="fp",
        credential_class=CredentialClass.READONLY,
        expires_at=None,
        scope_verified=scope_verified,
    )


@dataclass
class FakeConnectionRepository:
    """In-memory stand-in for `ConnectionRepository`. `list_exc`/`binding_exc`,
    when set, are raised on the matching call before anything is returned --
    simulating a read-path failure (DB drop, timeout) instead of a
    successful read."""

    connections: list[AccountConnection] = field(default_factory=list)
    bindings: dict[UUID, CredentialBinding | None] = field(default_factory=dict)
    list_exc: Exception | None = None
    binding_exc: Exception | None = None
    list_connections_calls: int = 0
    get_credential_binding_calls: int = 0

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        self.list_connections_calls += 1
        if self.list_exc is not None:
            raise self.list_exc
        return [c for c in self.connections if c.tenant_id == tenant_id]

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None:
        self.get_credential_binding_calls += 1
        if self.binding_exc is not None:
            raise self.binding_exc
        return self.bindings.get(connection_id)

    # Unused Protocol members -- not exercised by build_connection_list_view().
    async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
        raise NotImplementedError

    async def insert_pending_connection(self, connection: AccountConnection) -> AccountConnection:
        raise NotImplementedError

    async def transition_connection_state(
        self, connection_id: UUID, *, tenant_id: UUID, expected_state: str, new_state: str
    ) -> AccountConnection:
        raise NotImplementedError

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent:
        raise NotImplementedError

    async def insert_credential_binding(self, binding: CredentialBinding) -> CredentialBinding:
        raise NotImplementedError

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        raise NotImplementedError

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        tenant_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        raise NotImplementedError

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None:
        raise NotImplementedError

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth:
        raise NotImplementedError

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_build_connection_list_view_empty_tenant_returns_empty_view() -> None:
    """Boundary -- a tenant with zero connections must still get a
    `ConnectionListView` with `as_of` populated, not None/omitted, and no
    per-connection binding lookup should fire."""
    tenant_id = uuid4()
    repo = FakeConnectionRepository()

    view = await build_connection_list_view(repo, tenant_id)

    assert view.connections == []
    assert view.as_of is not None
    assert view.as_of.tzinfo is not None
    assert repo.get_credential_binding_calls == 0


async def test_build_connection_list_view_filters_to_requested_tenant_only() -> None:
    """Cross-tenant isolation at the projection boundary -- a connection
    belonging to a different tenant must never leak into this tenant's
    view even if the fake repo holds both."""
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    repo = FakeConnectionRepository(
        connections=[_connection(tenant_id), _connection(other_tenant_id)]
    )

    view = await build_connection_list_view(repo, tenant_id)

    assert len(view.connections) == 1


async def test_build_connection_list_view_defaults_scope_verified_false_without_binding() -> None:
    """A connection with no stored credential binding (e.g. still
    PENDING_CONSENT) must resolve to `scope_verified=False`, not raise or
    leave the field unset."""
    tenant_id = uuid4()
    connection = _connection(tenant_id)
    repo = FakeConnectionRepository(connections=[connection], bindings={})

    view = await build_connection_list_view(repo, tenant_id)

    assert len(view.connections) == 1
    assert view.connections[0].scope_verified is False


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_build_connection_list_view_propagates_list_connections_failure() -> None:
    """DB read-path failure injection on the first call -- must not be
    swallowed, and the per-connection binding fan-out must never start."""
    tenant_id = uuid4()
    repo = FakeConnectionRepository(list_exc=ConnectionResetError("simulated connection drop"))

    with pytest.raises(ConnectionResetError):
        await build_connection_list_view(repo, tenant_id)

    assert repo.get_credential_binding_calls == 0


async def test_build_connection_list_view_propagates_credential_binding_failure() -> None:
    """DB read-path failure injection on the per-connection fan-out call --
    must not be swallowed into a partial/silently-empty view."""
    tenant_id = uuid4()
    connection = _connection(tenant_id)
    repo = FakeConnectionRepository(
        connections=[connection], binding_exc=TimeoutError("simulated binding lookup timeout")
    )

    with pytest.raises(TimeoutError):
        await build_connection_list_view(repo, tenant_id)


# ---------------------------------------------------------------------------
# success path + pure helper
# ---------------------------------------------------------------------------


async def test_build_connection_list_view_success_maps_binding_scope_verified() -> None:
    tenant_id = uuid4()
    connection = _connection(tenant_id)
    binding = _binding(connection.id, scope_verified=True)
    repo = FakeConnectionRepository(connections=[connection], bindings={connection.id: binding})

    view = await build_connection_list_view(repo, tenant_id)

    assert len(view.connections) == 1
    assert view.connections[0].id == connection.id
    assert view.connections[0].scope_verified is True
    assert repo.list_connections_calls == 1
    assert repo.get_credential_binding_calls == 1


def test_connection_list_view_constructor_stores_fields_verbatim() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    view = ConnectionListView(connections=[], as_of=as_of)

    assert view.connections == []
    assert view.as_of is as_of


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_build_connection_list_view_perf_budget_p95_latency() -> None:
    """No published per-axis budget covers FND-05 read-model projections
    specifically (ADR-2026-09-09-C Decision 1's table); pin the same order
    of magnitude as the closest published read budget ("read model query
    p95 100ms") since this is a single list read plus a small in-memory
    fan-out, all served in-memory here."""
    tenant_id = uuid4()
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        connections = [_connection(tenant_id) for _ in range(5)]
        bindings: dict[UUID, CredentialBinding | None] = {c.id: _binding(c.id) for c in connections}
        repo = FakeConnectionRepository(connections=connections, bindings=bindings)

        start = time.perf_counter()
        await build_connection_list_view(repo, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 100.0, f"build_connection_list_view p95 latency {p95:.3f}ms exceeded 100ms budget"
