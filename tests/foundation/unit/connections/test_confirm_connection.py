"""FND-05 `application/confirm_connection.py` -- mocked failure injection +
perf assertion + gate-red repro (same reasoning as
`test_begin_connection_application.py`, task-4636/4646).

confirm_connection() folds two 74 §2 transitions (PENDING_CONSENT ->
CONNECTING -> ACTIVE_READONLY) plus a provider round trip and a credential
insert into one command (see module docstring "scope reduction"). None of
that ordering/fail-closed behaviour is covered by `test_rules.py` (pure
rules only) -- this file isolates the application layer behind in-memory
fakes so provider/repository failures can be forced on demand and the
transition order asserted directly.

# ratchet-allow: fail-closed Protocol stub methods (FakeConnectionRepository
# members confirm_connection() never calls) raise NotImplementedError so an
# accidental call surfaces immediately instead of returning a silently wrong
# fake value.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.security.encryption import legacy_decrypt
from src.foundation.connections.application.confirm_connection import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
    ForbiddenCapabilityScopeError,
    ScopeVerificationFailedError,
    confirm_connection,
)
from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    CapabilityScope,
    ConnectionConsent,
    ConnectionHealth,
    ConnectionState,
    CredentialBinding,
    CredentialClass,
    ProviderSnapshot,
    ScopeProof,
)
from src.foundation.connections.domain.rules import compute_scope_fingerprint

_NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
_ENCRYPTION_KEY = "22" * 32


class _ConcurrencyConflictError(Exception):
    """Stand-in for the real conditional-UPDATE conflict the postgres
    adapter raises when `expected_state` no longer matches (standard-105)."""


@dataclass
class FakeConnectionRepository:
    connection: AccountConnection | None
    transition_exc: Exception | None = None
    transition_calls: list[tuple[str, str]] = field(default_factory=list)
    insert_credential_binding_calls: int = 0
    inserted_binding: CredentialBinding | None = None
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
            raise _ConcurrencyConflictError(
                f"expected {expected_state}, actual {self._state}"
            )
        self._state = ConnectionState(new_state)
        assert self.connection is not None
        return replace(self.connection, state=self._state)

    async def insert_credential_binding(self, binding: CredentialBinding) -> CredentialBinding:
        self.insert_credential_binding_calls += 1
        self.inserted_binding = binding
        return binding

    # Unused Protocol members -- not exercised by confirm_connection().
    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        raise NotImplementedError
    async def insert_pending_connection(self, connection: AccountConnection) -> AccountConnection:
        raise NotImplementedError
    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent:
        raise NotImplementedError
    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None:
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


@dataclass
class FakeProvider:
    proof: ScopeProof | None = None
    verify_exc: Exception | None = None
    verify_calls: int = 0

    async def verify_readonly_scope(self, lease: Any) -> ScopeProof:
        self.verify_calls += 1
        if self.verify_exc is not None:
            raise self.verify_exc
        assert self.proof is not None
        return self.proof

    async def fetch_snapshot(self, account_ref: Any, as_of: datetime) -> ProviderSnapshot:
        raise NotImplementedError


def _connection(
    *,
    tenant_id: UUID,
    state: ConnectionState = ConnectionState.PENDING_CONSENT,
    capability_profile: tuple[CapabilityScope, ...] = (CapabilityScope.READ_BALANCE,),
) -> AccountConnection:
    return AccountConnection(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        provider_code="BITGET",
        opaque_account_ref="ACCT1234567890",
        state=state,
        capability_profile=capability_profile,
        revision=1,
        created_at=_NOW,
    )


def _proof(
    *,
    granted_scopes: tuple[CapabilityScope, ...] = (CapabilityScope.READ_BALANCE,),
    provider_credential_ref: str = "fake-cred-abc123",
    provider_verified: bool = True,
) -> ScopeProof:
    return ScopeProof(
        granted_scopes=granted_scopes,
        provider_credential_ref=provider_credential_ref,
        provider_verified=provider_verified,
    )


def _kwargs(tenant_id: UUID, connection_id: UUID, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        tenant_id=tenant_id,
        connection_id=connection_id,
        encryption_key=_ENCRYPTION_KEY,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_confirm_connection_raises_when_connection_not_found() -> None:
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository(connection=None)
    provider = FakeProvider(proof=_proof())

    with pytest.raises(ConnectionNotFoundError):
        await confirm_connection(
            conn_repo, provider, **_kwargs(tenant_id, uuid4())
        )

    assert conn_repo.transition_calls == []
    assert provider.verify_calls == 0


async def test_confirm_connection_rejects_cross_tenant_access() -> None:
    """73번 TRU-006 원칙 -- 다른 tenant의 connection은 존재 여부도 흘리지
    않고 거부한다. 어떤 상태 전이/provider 호출도 일어나선 안 된다."""
    owner_tenant_id = uuid4()
    attacker_tenant_id = uuid4()
    connection = _connection(tenant_id=owner_tenant_id)
    conn_repo = FakeConnectionRepository(connection=connection)
    provider = FakeProvider(proof=_proof())

    with pytest.raises(CrossTenantConnectionAccessError):
        await confirm_connection(
            conn_repo, provider, **_kwargs(attacker_tenant_id, connection.id)
        )

    assert conn_repo.transition_calls == []
    assert provider.verify_calls == 0


async def test_confirm_connection_rejects_scope_drift_without_activating() -> None:
    """CON-002 계열 -- provider가 요청보다 넓은 scope를 부여하면(scope
    drift) 자격증명을 저장하거나 ACTIVE_READONLY로 전이하기 전에 거부한다."""
    tenant_id = uuid4()
    connection = _connection(
        tenant_id=tenant_id, capability_profile=(CapabilityScope.READ_BALANCE,)
    )
    conn_repo = FakeConnectionRepository(connection=connection)
    provider = FakeProvider(
        proof=_proof(
            granted_scopes=(CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
        )
    )

    with pytest.raises(ForbiddenCapabilityScopeError) as exc_info:
        await confirm_connection(
            conn_repo, provider, **_kwargs(tenant_id, connection.id)
        )

    assert "READ_POSITION" in exc_info.value.rejected
    # first transition (PENDING_CONSENT -> CONNECTING) already ran before the
    # provider round trip, but the second (-> ACTIVE_READONLY) must not.
    assert conn_repo.transition_calls == [("PENDING_CONSENT", "CONNECTING")]
    assert conn_repo.insert_credential_binding_calls == 0


async def test_confirm_connection_propagates_conflict_when_state_already_advanced() -> None:
    """상태가 이미 PENDING_CONSENT가 아니면(동시 재시도 등) conditional
    UPDATE가 0 rows로 실패하고, 그 실패를 삼키지 않고 그대로 전파해야 한다
    -- provider 호출 자체가 일어나선 안 된다(불필요한 외부 side effect)."""
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id, state=ConnectionState.CONNECTING)
    conn_repo = FakeConnectionRepository(connection=connection)
    provider = FakeProvider(proof=_proof())

    with pytest.raises(_ConcurrencyConflictError):
        await confirm_connection(
            conn_repo, provider, **_kwargs(tenant_id, connection.id)
        )

    assert provider.verify_calls == 0
    assert conn_repo.insert_credential_binding_calls == 0


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_confirm_connection_wraps_provider_failure_fail_closed() -> None:
    """Provider handshake failure (network drop, malformed response, etc.)
    must not be swallowed and must not fall through to a credential insert
    or the second (-> ACTIVE_READONLY) transition."""
    tenant_id = uuid4()
    connection = _connection(tenant_id=tenant_id)
    conn_repo = FakeConnectionRepository(connection=connection)
    provider = FakeProvider(verify_exc=ConnectionResetError("simulated handshake drop"))

    with pytest.raises(ScopeVerificationFailedError):
        await confirm_connection(
            conn_repo, provider, **_kwargs(tenant_id, connection.id)
        )

    assert conn_repo.transition_calls == [("PENDING_CONSENT", "CONNECTING")]
    assert conn_repo.insert_credential_binding_calls == 0


# ---------------------------------------------------------------------------
# success path (D2)
# ---------------------------------------------------------------------------


async def test_confirm_connection_success_activates_and_stores_encrypted_binding() -> None:
    tenant_id = uuid4()
    scopes = (CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
    connection = _connection(tenant_id=tenant_id, capability_profile=scopes)
    conn_repo = FakeConnectionRepository(connection=connection)
    proof = _proof(granted_scopes=scopes, provider_verified=True)
    provider = FakeProvider(proof=proof)

    view = await confirm_connection(
        conn_repo, provider, **_kwargs(tenant_id, connection.id)
    )

    assert view.state.value == "ACTIVE_READONLY"
    assert view.scope_verified is True
    assert conn_repo.transition_calls == [
        ("PENDING_CONSENT", "CONNECTING"),
        ("CONNECTING", "ACTIVE_READONLY"),
    ]
    assert conn_repo.insert_credential_binding_calls == 1

    binding = conn_repo.inserted_binding
    assert binding is not None
    assert binding.connection_id == connection.id
    assert binding.credential_class == CredentialClass.READONLY
    assert binding.scope_fingerprint == compute_scope_fingerprint(scopes)
    assert binding.scope_verified is True
    # vault_secret_ref is ciphertext, never the raw provider_credential_ref.
    assert "fake-cred-abc123" not in binding.vault_secret_ref
    assert legacy_decrypt(binding.vault_secret_ref, _ENCRYPTION_KEY) == "fake-cred-abc123"


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves detect_scope_drift is load-bearing for
# confirm_connection's fail-closed activation gate.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_scope_drift_check_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    connection = _connection(
        tenant_id=tenant_id, capability_profile=(CapabilityScope.READ_BALANCE,)
    )
    provider = FakeProvider(
        proof=_proof(
            granted_scopes=(CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
        )
    )

    # green: guard active -- wider-than-requested grant is rejected, no
    # credential is ever persisted.
    conn_repo = FakeConnectionRepository(connection=connection)
    with pytest.raises(ForbiddenCapabilityScopeError):
        await confirm_connection(conn_repo, provider, **_kwargs(tenant_id, connection.id))
    assert conn_repo.insert_credential_binding_calls == 0

    # red repro: neutralize exactly the drift check confirm_connection
    # delegates to (detect_scope_drift) by forcing it to always report "no
    # drift". Without this guard, a connection would activate with a wider
    # granted scope than the tenant actually consented to.
    import src.foundation.connections.application.confirm_connection as target

    monkeypatch.setattr(target, "detect_scope_drift", lambda requested, granted: False)

    conn_repo2 = FakeConnectionRepository(connection=connection)
    provider2 = FakeProvider(
        proof=_proof(
            granted_scopes=(CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
        )
    )
    view = await confirm_connection(conn_repo2, provider2, **_kwargs(tenant_id, connection.id))

    assert view.state.value == "ACTIVE_READONLY"
    assert conn_repo2.insert_credential_binding_calls == 1


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_confirm_connection_perf_budget_p95_latency() -> None:
    """No published per-axis budget covers FND-05 specifically (ADR-2026-09-09-C
    Decision 1's table); pin the same order of magnitude as the closest
    published command-write budget ("order submit -> ACK p95 50ms, paper")
    since this command is also two fail-closed writes plus one provider
    round trip, all served in-memory here."""
    tenant_id = uuid4()
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        connection = _connection(tenant_id=tenant_id)
        conn_repo = FakeConnectionRepository(connection=connection)
        provider = FakeProvider(proof=_proof())

        start = time.perf_counter()
        await confirm_connection(conn_repo, provider, **_kwargs(tenant_id, connection.id))
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, f"confirm_connection p95 latency {p95:.3f}ms exceeded 50ms budget"
