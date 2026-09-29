"""FND-08 Reconciliation & Resilience 통합테스트 — REC-007 resolve 부정/
실패주입/성능 케이스. test_reconciliation_lifecycle.py의 REC-001~004 lifecycle
분류 케이스에서 분리했다(loc_over_500, task-8905)."""

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


async def test_resolve_rejects_missing_target_without_writing(pool, repo, monkeypatch):
    """REC-007 negative: a missing target cannot become RESOLVED."""
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)

    with pytest.raises(ReconciliationStateNotFoundError, match=str(target_ref)):
        await resolve_reconciliation(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            target_ref=target_ref,
            reason="missing target",
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
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=tenant_id,
        connection_id=None,
        entities=entities,
    )
    before = await repo.get_state(tenant_id)
    control_before = (
        await risk_repo.get_safety_control(before.safety_control_id)
        if before.safety_control_id
        else None
    )
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)
    error = (
        CrossTenantReconciliationAccessError if rejection == "cross_tenant" else NotResolvableError
    )
    message = str(tenant_id) if rejection == "cross_tenant" else "HEALTHY status"

    with pytest.raises(error, match=message):
        await resolve_reconciliation(
            repo,
            tenant_id=caller_id,
            actor_subject_id=caller_id,
            target_ref=tenant_id,
            reason="invalid resolve request",
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
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=tenant_id,
        connection_id=None,
        entities=_mismatched_entities(),
    )
    before = await repo.get_state(tenant_id)
    control_before = await risk_repo.get_safety_control(before.safety_control_id)
    failure = RuntimeError("injected transition storage failure")
    transition = AsyncMock(side_effect=failure)
    with monkeypatch.context() as patch:
        patch.setattr(repo, "transition_state_status", transition)
        with pytest.raises(RuntimeError, match="injected transition storage failure") as caught:
            await resolve_reconciliation(
                repo,
                tenant_id=tenant_id,
                actor_subject_id=tenant_id,
                target_ref=tenant_id,
                reason="first attempt",
            )
        assert caught.value is failure
        transition.assert_awaited_once()

    assert await repo.get_state(tenant_id) == before
    assert before.blocking_reason is not None
    assert control_before.state.value == "ACTIVE"
    assert await risk_repo.get_safety_control(before.safety_control_id) == control_before

    resolved = await resolve_reconciliation(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        target_ref=tenant_id,
        reason="retry after storage recovery",
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
                repo,
                tenant_id=tenant_id,
                actor_subject_id=tenant_id,
                target_ref=uuid4(),
                reason="rejection latency probe",
            )
        durations.append(perf_counter() - started)
    p95 = sorted(durations)[18]
    assert p95 < 0.8, f"resolve rejection p95={p95:.3f}s exceeds 800ms mutation budget"
