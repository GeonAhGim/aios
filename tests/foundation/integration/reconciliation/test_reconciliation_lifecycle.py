"""FND-08 Reconciliation & Resilience 통합테스트 — 실제 dev DB 대상. 80번 §4
중 실 스케줄러/내부 원장 없이 재현 가능한 범위(REC-001~004, 006~007)."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from time import perf_counter
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.domain.models import AccountConnection, CapabilityScope
from src.foundation.connections.domain.models import ConnectionState as ConnState
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
    resolve_reconciliation,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.reconciliation.contracts.v1 import EntitySnapshot
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresReconciliationRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


async def _tenant(pool):
    return await create_test_tenant(pool)


def _matching_entities() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT",
            internal_value=Decimal("1000.00"),
            provider_value=Decimal("1000.00"),
        )
    ]


def _mismatched_entities() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT",
            internal_value=Decimal("1000.00"),
            provider_value=Decimal("400.00"),
        )
    ]


async def test_matching_values_creates_healthy_run(pool, repo, connection_repo, risk_repo):
    """REC-001 — matching order/fill/position/cash creates HEALTHY
    evidence/projection."""
    tenant_id = await _tenant(pool)
    target_ref = tenant_id  # 별도 deployment 없이 tenant 자체를 target으로 재사용

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=_matching_entities(),
    )
    assert run.aggregate_classification.value == "HEALTHY"

    state = await repo.get_state(target_ref)
    assert state.aggregate_status.value == "HEALTHY"
    assert state.safety_control_id is None


async def test_material_mismatch_activates_safety_control_and_blocks(
    pool, repo, connection_repo, risk_repo
):
    """REC-002 — material fill/balance mismatch pauses target before new
    submission. STRATEGY_DEPLOYMENT 범위 kill switch가 실제로 걸리는지는
    `get_safety_control()`로 직접 확인한다 — risk_gate.list_active_controls()는
    아직 STRATEGY_DEPLOYMENT를 조회 대상에 포함하지 않는다(risk_gate 자체의
    기존 gap, #2026-09-02-28 — evaluate_risk_gate가 deployment 범위 평가
    경로를 아직 안 가짐; run_reconciliation.py 상단 docstring 참조)."""
    tenant_id = await _tenant(pool)
    target_ref = tenant_id

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=_mismatched_entities(),
    )
    assert run.aggregate_classification.value == "MATERIAL_MISMATCH"

    state = await repo.get_state(target_ref)
    assert state.aggregate_status.value == "MATERIAL_MISMATCH"
    assert state.safety_control_id is not None
    assert state.blocking_reason is not None

    control = await risk_repo.get_safety_control(state.safety_control_id)
    assert control is not None
    assert control.state.value == "ACTIVE"
    assert control.scope.value == "STRATEGY_DEPLOYMENT"
    assert control.scope_ref == str(target_ref)


async def test_duplicate_run_dedupes_and_does_not_reactivate_control(
    pool, repo, connection_repo, risk_repo
):
    """REC-004/006 — concurrent/duplicate runs dedupe, safe retry does not
    duplicate a control activation."""
    tenant_id = await _tenant(pool)
    target_ref = tenant_id

    first = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=_mismatched_entities(),
    )
    second = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=_mismatched_entities(),
    )
    assert first.id == second.id

    first_state = await repo.get_state(target_ref)
    # 두 번째 호출이 dedup됐다면(같은 run 반환) upsert_state도 다시
    # 호출되지 않아야 한다 — revision이 여전히 0이면 안전 통제 활성화도
    # 딱 한 번뿐이었다는 뜻(activate_safety_control은 매번 새 control
    # 행을 만들므로, 두 번 불렸다면 fence_token이 2 이상이었을 것).
    control = await risk_repo.get_safety_control(first_state.safety_control_id)
    assert control.fence_token == 1


async def test_resolve_does_not_reactivate_or_clear_safety_control(
    pool, repo, connection_repo, risk_repo
):
    """REC-007 — resolve alone cannot resume; safety_control은 resolve로
    건드리지 않는다(별도 deactivate_safety_control 호출이 필요)."""
    tenant_id = await _tenant(pool)
    target_ref = tenant_id

    await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=_mismatched_entities(),
    )
    state_before = await repo.get_state(target_ref)

    resolved = await resolve_reconciliation(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        target_ref=target_ref,
        reason="원인 파악 완료, 수동 정정함",
    )
    assert resolved.aggregate_status.value == "RESOLVED"

    control = await risk_repo.get_safety_control(state_before.safety_control_id)
    assert control.state.value == "ACTIVE"

    with pytest.raises(NotResolvableError):
        await resolve_reconciliation(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            target_ref=target_ref,
            reason="다시 resolve 시도",
        )


async def test_connection_unavailable_marks_all_items_unavailable(
    pool, repo, connection_repo, risk_repo
):
    """REC-003 — provider timeout/partial payload marks unavailable, no
    zero-state assumption. connection이 아예 없으면(get_latest_health가
    None) unhealthy로 취급한다."""
    tenant_id = await _tenant(pool)
    connection = await connection_repo.insert_pending_connection(
        AccountConnection(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_subject_id=tenant_id,
            provider_code="fake-broker",
            opaque_account_ref="ACCT-rec-1",
            state=ConnState.PENDING_CONSENT,
            capability_profile=(CapabilityScope.READ_BALANCE,),
            revision=1,
        )
    )

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=tenant_id,
        connection_id=connection.id,
        entities=_matching_entities(),
    )
    assert run.aggregate_classification.value == "PROVIDER_UNAVAILABLE"
    assert all(item.classification.value == "PROVIDER_UNAVAILABLE" for item in run.items)


async def test_resolve_rejects_missing_target_without_writing(pool, repo, monkeypatch):
    """REC-007 negative: a missing target cannot become RESOLVED."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)

    with pytest.raises(ReconciliationStateNotFoundError, match=str(target_ref)):
        await resolve_reconciliation(
            repo, tenant_id=tenant_id, actor_subject_id=tenant_id,
            target_ref=target_ref, reason="missing target",
        )

    transition.assert_not_awaited()
    assert await repo.get_state(target_ref) is None


@pytest.mark.parametrize("rejection", ["cross_tenant", "healthy"])
async def test_resolve_rejects_invalid_request_without_mutation(
    pool, repo, connection_repo, risk_repo, monkeypatch, rejection
):
    """REC-007 negative/adversarial: tenant and lifecycle gates precede writes (I-10)."""
    tenant_id = await _tenant(pool)
    caller_id = await _tenant(pool) if rejection == "cross_tenant" else tenant_id
    entities = _mismatched_entities() if rejection == "cross_tenant" else _matching_entities()
    await run_reconciliation(
        repo, connection_repo, risk_repo, tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT", target_ref=tenant_id,
        connection_id=None, entities=entities,
    )
    before = await repo.get_state(tenant_id)
    control_before = (
        await risk_repo.get_safety_control(before.safety_control_id)
        if before.safety_control_id else None
    )
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)
    error = (
        CrossTenantReconciliationAccessError
        if rejection == "cross_tenant" else NotResolvableError
    )
    message = str(tenant_id) if rejection == "cross_tenant" else "HEALTHY status"

    with pytest.raises(error, match=message):
        await resolve_reconciliation(
            repo, tenant_id=caller_id, actor_subject_id=caller_id,
            target_ref=tenant_id, reason="invalid resolve request",
        )

    transition.assert_not_awaited()
    assert await repo.get_state(tenant_id) == before
    if control_before is not None:
        assert control_before.state.value == "ACTIVE"
        assert await risk_repo.get_safety_control(before.safety_control_id) == control_before


async def test_resolve_failure_injection_preserves_block_and_retry(
    pool, repo, connection_repo, risk_repo, monkeypatch
):
    """REC-007/I-10: persistence failure propagates; retry never disarms safety."""
    tenant_id = await _tenant(pool)
    await run_reconciliation(
        repo, connection_repo, risk_repo, tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT", target_ref=tenant_id,
        connection_id=None, entities=_mismatched_entities(),
    )
    before = await repo.get_state(tenant_id)
    control_before = await risk_repo.get_safety_control(before.safety_control_id)
    failure = RuntimeError("injected transition storage failure")
    transition = AsyncMock(side_effect=failure)
    with monkeypatch.context() as patch:
        patch.setattr(repo, "transition_state_status", transition)
        with pytest.raises(RuntimeError, match="injected transition storage failure") as caught:
            await resolve_reconciliation(
                repo, tenant_id=tenant_id, actor_subject_id=tenant_id,
                target_ref=tenant_id, reason="first attempt",
            )
        assert caught.value is failure
        transition.assert_awaited_once()

    assert await repo.get_state(tenant_id) == before
    assert before.blocking_reason is not None
    assert control_before.state.value == "ACTIVE"
    assert await risk_repo.get_safety_control(before.safety_control_id) == control_before

    resolved = await resolve_reconciliation(
        repo, tenant_id=tenant_id, actor_subject_id=tenant_id,
        target_ref=tenant_id, reason="retry after storage recovery",
    )
    assert resolved.aggregate_status.value == "RESOLVED"
    after = await repo.get_state(tenant_id)
    assert after.revision == before.revision + 1
    assert after.resolved_by == tenant_id
    assert after.resolution_reason == "retry after storage recovery"
    assert after.safety_control_id == before.safety_control_id
    assert await risk_repo.get_safety_control(before.safety_control_id) == control_before


@pytest.mark.perf
async def test_resolve_missing_target_rejection_p95_budget(pool, repo):
    """PLT mutation budget: DB-backed rejection alone must fit p95 < 800ms.

    `perf` marker (task-7434 guard): this is a wall-clock budget over DB round
    trips, so it runs in the serial perf stage, not under xdist core contention.

    This measures the command, not the full HTTP route; the route budget is
    defined in L4_platform_observability_tenancy_api_v1.0.md section 7.
    """
    tenant_id = await _tenant(pool)
    durations = []
    for _ in range(20):
        started = perf_counter()
        with pytest.raises(ReconciliationStateNotFoundError):
            await resolve_reconciliation(
                repo, tenant_id=tenant_id, actor_subject_id=tenant_id,
                target_ref=uuid4(), reason="rejection latency probe",
            )
        durations.append(perf_counter() - started)
    p95 = sorted(durations)[18]
    assert p95 < 0.8, f"resolve rejection p95={p95:.3f}s exceeds 800ms mutation budget"
