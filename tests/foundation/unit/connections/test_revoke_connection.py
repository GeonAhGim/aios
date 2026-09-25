"""FND-05 `application/revoke_connection.py` -- mocked failure injection +
perf assertion + gate-red repro (same reasoning as
`test_begin_connection_application.py`/`test_confirm_connection.py`,
task-4636/4646/4661).

revoke_connection() gates a single conditional-UPDATE transition
(ACTIVE_READONLY|DEGRADED -> REVOKED) behind `_REVOCABLE_STATES`, then calls
`revoke_credential_binding()` (74 §2 "vault revoke" substitute -- pulls
expires_at into the past, see module docstring). None of that
ordering/fail-closed behaviour is covered by `test_rules.py` (pure rules
only) -- this file isolates the application layer behind an in-memory fake
so repository failures can be forced on demand and the transition/credential
call order asserted directly.

# ratchet-allow: fail-closed Protocol stub methods (FakeConnectionRepository
# members revoke_connection() never calls) raise NotImplementedError so an
# accidental call surfaces immediately instead of returning a silently wrong
# fake value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.application.revoke_connection import (
    ConnectionNotRevocableError,
    revoke_connection,
)
from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    ConnectionConsent,
    ConnectionHealth,
    ConnectionState,
    CredentialBinding,
)

_NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


class _ConcurrencyConflictError(Exception):
    """Stand-in for the real conditional-UPDATE conflict the postgres
    adapter raises when `expected_state` no longer matches (standard-105)."""


@dataclass
class FakeConnectionRepository:
    connection: AccountConnection | None
    transition_exc: Exception | None = None
    revoke_binding_exc: Exception | None = None
    transition_calls: list[tuple[str, str]] = field(default_factory=list)
    revoke_binding_calls: int = 0
    _state: ConnectionState | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._state = self.connection.state if self.connection is not None else None

    async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
        return self.connection

    async def transition_connection_state(
        self, connection_id: UUID, *, tenant_id: UUID, expected_state: str, new_state: str
    ) -> AccountConnection:
        self.transition_calls.append((expected_state, new_state))
        if self.transition_exc is not None:
            raise self.transition_exc
        if self._state is None or self._state.value != expected_state:
            raise _ConcurrencyConflictError(f"expected {expected_state}, actual {self._state}")
        self._state = ConnectionState(new_state)
        assert self.connection is not None
        return replace(self.connection, state=self._state)

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        self.revoke_binding_calls += 1
        if self.revoke_binding_exc is not None:
            raise self.revoke_binding_exc

    # Unused Protocol members -- not exercised by revoke_connection().
    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        raise NotImplementedError

    async def insert_pending_connection(self, connection: AccountConnection) -> AccountConnection:
        raise NotImplementedError

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent:
        raise NotImplementedError

    async def insert_credential_binding(self, binding: CredentialBinding) -> CredentialBinding:
        raise NotImplementedError

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None:
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


def _connection(
    *,
    tenant_id: UUID,
    state: ConnectionState = ConnectionState.ACTIVE_READONLY,
) -> AccountConnection:
    return AccountConnection(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        provider_code="BITGET",
        opaque_account_ref="ACCT1234567890",
        state=state,
        capability_profile=(),
        revision=1,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_revoke_connection_raises_when_connection_not_found() -> None:
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository(connection=None)

    with pytest.raises(ConnectionNotFoundError):
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=uuid4())

    assert conn_repo.transition_calls == []
    assert conn_repo.revoke_binding_calls == 0


async def test_revoke_connection_rejects_cross_tenant_access() -> None:
    """73번 TRU-006 원칙 -- 다른 tenant의 connection은 존재 여부도 흘리지
    않고 거부한다. 어떤 상태 전이/credential 해지도 일어나선 안 된다."""
    owner_tenant_id = uuid4()
    attacker_tenant_id = uuid4()
    connection = _connection(tenant_id=owner_tenant_id)
    conn_repo = FakeConnectionRepository(connection=connection)

    with pytest.raises(CrossTenantConnectionAccessError):
        await revoke_connection(
            conn_repo, tenant_id=attacker_tenant_id, connection_id=connection.id
        )

    assert conn_repo.transition_calls == []
    assert conn_repo.revoke_binding_calls == 0


@pytest.mark.parametrize(
    "state",
    [
        ConnectionState.PENDING_CONSENT,
        ConnectionState.CONNECTING,
        ConnectionState.REVOKED,
        ConnectionState.DISCONNECTED,
    ],
)
async def test_revoke_connection_rejects_non_revocable_states(
    state: ConnectionState,
) -> None:
    """Only ACTIVE_READONLY/DEGRADED are revocable -- every other state
    (including already-REVOKED, guarding against a double-revoke) must be
    rejected before any write is attempted."""
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=state)
    conn_repo = FakeConnectionRepository(connection=connection)

    with pytest.raises(ConnectionNotRevocableError):
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)

    assert conn_repo.transition_calls == []
    assert conn_repo.revoke_binding_calls == 0


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_revoke_connection_propagates_conflict_when_state_already_advanced() -> None:
    """상태가 이미 바뀌어 있으면(동시 재시도 등) conditional UPDATE가 0
    rows로 실패하고, 그 실패를 삼키지 않고 그대로 전파해야 한다 --
    credential binding 해지가 일어나선 안 된다."""
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=ConnectionState.ACTIVE_READONLY)
    conn_repo = FakeConnectionRepository(
        connection=connection, transition_exc=_ConcurrencyConflictError("stale")
    )

    with pytest.raises(_ConcurrencyConflictError):
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)

    assert conn_repo.transition_calls == [("ACTIVE_READONLY", "REVOKED")]
    assert conn_repo.revoke_binding_calls == 0


async def test_revoke_connection_propagates_credential_binding_revoke_failure() -> None:
    """State transition succeeds but the credential-binding revoke write
    fails (DB drop) -- must not be swallowed. Fail-closed: caller sees the
    connection is REVOKED-transitioned but the binding revoke itself
    surfaces the error rather than reporting success."""
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=ConnectionState.DEGRADED)
    conn_repo = FakeConnectionRepository(
        connection=connection,
        revoke_binding_exc=ConnectionResetError("simulated connection drop"),
    )

    with pytest.raises(ConnectionResetError):
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)

    assert conn_repo.transition_calls == [("DEGRADED", "REVOKED")]
    assert conn_repo.revoke_binding_calls == 1


# ---------------------------------------------------------------------------
# success path (D2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ConnectionState.ACTIVE_READONLY, ConnectionState.DEGRADED])
async def test_revoke_connection_success_transitions_and_revokes_binding(
    state: ConnectionState,
) -> None:
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=state)
    conn_repo = FakeConnectionRepository(connection=connection)

    view = await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)

    assert view.state.value == "REVOKED"
    assert conn_repo.transition_calls == [(state.value, "REVOKED")]
    assert conn_repo.revoke_binding_calls == 1


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves _REVOCABLE_STATES is load-bearing for
# revoke_connection's fail-closed state gate.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_revocable_states_check_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=ConnectionState.PENDING_CONSENT)
    conn_repo = FakeConnectionRepository(connection=connection)

    # green: guard active -- a not-yet-active connection cannot be revoked,
    # no state transition or credential-binding write ever fires.
    with pytest.raises(ConnectionNotRevocableError):
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)
    assert conn_repo.transition_calls == []
    assert conn_repo.revoke_binding_calls == 0

    # red repro: neutralize exactly the gate revoke_connection checks
    # (_REVOCABLE_STATES) by widening it to include every state. Without
    # this guard, a connection that never finished activation (no
    # credential binding to speak of) would be pushed straight to REVOKED.
    import src.foundation.connections.application.revoke_connection as target

    monkeypatch.setattr(target, "_REVOCABLE_STATES", frozenset(ConnectionState))

    conn_repo2 = FakeConnectionRepository(connection=connection)
    view = await revoke_connection(conn_repo2, tenant_id=tenant_id, connection_id=connection.id)

    assert view.state.value == "REVOKED"
    assert conn_repo2.transition_calls == [("PENDING_CONSENT", "REVOKED")]


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_revoke_connection_perf_budget_p95_latency() -> None:
    """No published per-axis budget covers FND-05 specifically (ADR-2026-09-09-C
    Decision 1's table); pin the same order of magnitude as the closest
    published command-write budget ("order submit -> ACK p95 50ms, paper")
    since this command is also a single fail-closed transition plus one
    dependent write, all served in-memory here."""
    tenant_id = uuid4()
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        connection = _connection(tenant_id=tenant_id)
        conn_repo = FakeConnectionRepository(connection=connection)

        start = time.perf_counter()
        await revoke_connection(conn_repo, tenant_id=tenant_id, connection_id=connection.id)
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, f"revoke_connection p95 latency {p95:.3f}ms exceeded 50ms budget"
