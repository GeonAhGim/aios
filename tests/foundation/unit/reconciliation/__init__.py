"""FND-08 Reconciliation & Resilience — run_reconciliation application-command unit
tests (fake repositories, no DB). Package __init__.py hosts the tests here to
follow this repo's established convention for DEEPEN passes on orphaned test
packages (see tests/foundation/integration/reconciliation/__init__.py,
tests/foundation/unit/paper_control/__init__.py).

Covers what test_models.py/test_rules.py/tests/unit/foundation/reconciliation/
test_resolve_reconciliation.py do not: the orchestration in
application/run_reconciliation.py — connection-health-forced PROVIDER_UNAVAILABLE
(REC-003), safety-control activation on a blocking classification (ticket 80 §1),
dedup by input hash (REC-004/006), and fail-closed propagation when a dependency
(repository insert/upsert, safety-control activation) fails.

# ratchet-allow: fake 리포지토리가 이 파일의 테스트가 쓰지 않는 인터페이스
# 메서드에 대해 fail-closed 스텁으로 NotImplementedError를 낸다.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.connections.domain.models import ConnectionHealth, HealthState
from src.foundation.reconciliation.application import (
    run_reconciliation as run_reconciliation_module,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.reconciliation.contracts.v1 import EntitySnapshot
from src.foundation.reconciliation.domain.models import Classification, ReconciliationRun
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.domain.models import SafetyControlState, SafetyScope


def _entity(**overrides) -> EntitySnapshot:
    fields = dict(
        entity_type="USDT_BALANCE",
        entity_key="acct-1",
        internal_value=Decimal("100.00"),
        provider_value=Decimal("100.00"),
    )
    fields.update(overrides)
    return EntitySnapshot(**fields)


def _health(state: HealthState) -> ConnectionHealth:
    return ConnectionHealth(
        connection_id=uuid4(),
        evaluated_at=datetime.now(timezone.utc),
        state=state,
    )


class _FakeReconciliationRepo:
    def __init__(self) -> None:
        self.runs_by_hash: dict[tuple[UUID, str], ReconciliationRun] = {}
        self.inserted: list[ReconciliationRun] = []
        self.states: list = []
        self.insert_error: Exception | None = None
        self.upsert_error: Exception | None = None

    async def get_run_by_input_hash(self, target_ref, input_hash, tenant_id):
        return self.runs_by_hash.get((target_ref, input_hash))

    async def insert_run_with_items(self, run, items):
        if self.insert_error is not None:
            raise self.insert_error
        stored = replace(run, items=items, created_at=datetime.now(timezone.utc))
        self.inserted.append(stored)
        self.runs_by_hash[(stored.target_ref, stored.input_hash)] = stored
        return stored

    async def get_state(self, target_ref):
        raise NotImplementedError

    async def list_states(self, tenant_id):
        raise NotImplementedError

    async def upsert_state(self, state):
        if self.upsert_error is not None:
            raise self.upsert_error
        self.states.append(state)
        return state

    async def transition_state_status(self, *args, **kwargs):
        raise NotImplementedError


class _FakeConnectionRepo:
    def __init__(self, health: ConnectionHealth | None) -> None:
        self._health = health
        self.calls: list[UUID] = []

    async def get_latest_health(self, connection_id):
        self.calls.append(connection_id)
        return self._health


def _patch_activate(monkeypatch: pytest.MonkeyPatch, control_id: UUID | None = None) -> UUID:
    """Stub out risk_gate's activate_safety_control — these unit tests exercise
    run_reconciliation's own orchestration, not risk_gate's persistence."""
    resolved_id = control_id or uuid4()

    async def _fake_activate(*_args, **kwargs):
        return SafetyControlView(
            id=resolved_id,
            scope=kwargs["scope"],
            scope_ref=kwargs["scope_ref"] or "",
            state=SafetyControlState.ACTIVE,
            reason=kwargs["reason"],
            fence_token=1,
            created_at=None,
            deactivated_at=None,
        )

    monkeypatch.setattr(run_reconciliation_module, "activate_safety_control", _fake_activate)
    return resolved_id


async def _run(
    repo: _FakeReconciliationRepo,
    conn_repo: _FakeConnectionRepo,
    *,
    entities: list[EntitySnapshot],
    connection_id: UUID | None,
    tenant_id: UUID | None = None,
    target_ref: UUID | None = None,
):
    return await run_reconciliation(
        repo,
        conn_repo,
        object(),  # risk_repo — unused when activate_safety_control is monkeypatched
        tenant_id=tenant_id or uuid4(),
        target_type="ACCOUNT",
        target_ref=target_ref or uuid4(),
        connection_id=connection_id,
        entities=entities,
    )


# --- negative: invariant-violating input must not classify as healthy --------


async def test_negative_missing_connection_health_forces_provider_unavailable(monkeypatch):
    """REC-003 — a provider health-check failure must never let matching
    internal/provider values pass through as HEALTHY."""
    _patch_activate(monkeypatch)
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=None)
    connection_id = uuid4()

    view = await _run(
        repo,
        conn_repo,
        entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("100"))],
        connection_id=connection_id,
    )

    assert view.items[0].classification.value == Classification.PROVIDER_UNAVAILABLE.value
    assert view.aggregate_classification.value == Classification.PROVIDER_UNAVAILABLE.value
    assert conn_repo.calls == [connection_id]


async def test_negative_degraded_connection_forces_provider_unavailable(monkeypatch):
    """A DEGRADED (not just missing) health record is equally untrustworthy —
    must not fall through to per-item value comparison."""
    _patch_activate(monkeypatch)
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.DEGRADED))

    view = await _run(
        repo,
        conn_repo,
        entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("100"))],
        connection_id=uuid4(),
    )

    assert view.aggregate_classification.value == Classification.PROVIDER_UNAVAILABLE.value


async def test_negative_material_mismatch_always_leaves_state_blocked(monkeypatch):
    """I-07 — a MATERIAL_MISMATCH aggregate must never resolve to an unblocked
    state; a safety control has to be activated and recorded on the state."""
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))
    control_id = uuid4()

    async def _fake_activate(*_args, **kwargs):
        assert kwargs["actor_is_admin"] is True
        assert kwargs["scope"] == SafetyScope.STRATEGY_DEPLOYMENT
        return SafetyControlView(
            id=control_id,
            scope=kwargs["scope"],
            scope_ref=kwargs["scope_ref"],
            state=SafetyControlState.ACTIVE,
            reason=kwargs["reason"],
            fence_token=1,
            created_at=None,
            deactivated_at=None,
        )

    monkeypatch.setattr(run_reconciliation_module, "activate_safety_control", _fake_activate)

    await _run(
        repo,
        conn_repo,
        entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("1"))],
        connection_id=uuid4(),
    )

    assert len(repo.states) == 1
    state = repo.states[0]
    assert state.blocking_reason == "INTEGRITY_RECONCILIATION_MISMATCH:MATERIAL_MISMATCH"
    assert state.safety_control_id == control_id
    assert state.last_healthy_at is None


# --- failure injection --------------------------------------------------------


async def test_failure_injection_insert_failure_prevents_state_write():
    """A storage failure on run insertion must propagate rather than being
    swallowed, and must never leave a partial/stale state behind it."""
    repo = _FakeReconciliationRepo()
    repo.insert_error = RuntimeError("injected insert failure")
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))

    with pytest.raises(RuntimeError, match="injected insert failure"):
        await _run(
            repo,
            conn_repo,
            entities=[_entity()],
            connection_id=uuid4(),
        )

    assert repo.states == []


async def test_failure_injection_safety_control_activation_failure_propagates(monkeypatch):
    """If activating the blocking safety control fails, run_reconciliation must
    not fall back to writing an unblocked/healthy state — fail closed."""
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))
    failure = RuntimeError("injected safety-control activation failure")

    async def _boom(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(run_reconciliation_module, "activate_safety_control", _boom)

    with pytest.raises(RuntimeError, match="injected safety-control activation failure") as caught:
        await _run(
            repo,
            conn_repo,
            entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("1"))],
            connection_id=uuid4(),
        )

    assert caught.value is failure
    assert len(repo.inserted) == 1  # run itself was persisted
    assert repo.states == []  # but no state was ever written for it


# --- control cases: fakes behave correctly on the non-blocking path ----------


async def test_run_reconciliation_healthy_values_do_not_trigger_safety_control():
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))

    view = await _run(
        repo,
        conn_repo,
        entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("100"))],
        connection_id=uuid4(),
    )

    assert view.aggregate_classification.value == Classification.HEALTHY.value
    assert len(repo.states) == 1
    assert repo.states[0].blocking_reason is None
    assert repo.states[0].safety_control_id is None


async def test_run_reconciliation_dedup_returns_cached_run_without_reinserting():
    """REC-004/006 — same target+input hash must short-circuit to the existing
    run instead of recomputing/reinserting."""
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))
    target_ref = uuid4()
    entities = [_entity(internal_value=Decimal("100"), provider_value=Decimal("100"))]

    first = await _run(
        repo, conn_repo, entities=entities, connection_id=uuid4(), target_ref=target_ref
    )
    assert len(repo.inserted) == 1

    second = await _run(
        repo, conn_repo, entities=entities, connection_id=uuid4(), target_ref=target_ref
    )

    assert second.id == first.id
    assert len(repo.inserted) == 1  # not re-inserted


# --- performance ---------------------------------------------------------------


@pytest.mark.perf
async def test_run_reconciliation_meets_latency_budget():
    """500 sequential runs against in-memory fakes stay well under 1s p50
    budget for pure orchestration logic with no real I/O (ADR-2026-09-09-C
    Decision 1 default)."""
    repo = _FakeReconciliationRepo()
    conn_repo = _FakeConnectionRepo(health=_health(HealthState.HEALTHY))

    start = time.perf_counter()
    for _ in range(500):
        await _run(
            repo,
            conn_repo,
            entities=[_entity(internal_value=Decimal("100"), provider_value=Decimal("100"))],
            connection_id=uuid4(),
        )
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
