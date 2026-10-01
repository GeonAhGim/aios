"""FND-08 Reconciliation resolve lifecycle test (L4_*#FND-08, REC-007).

Split from `test_reconciliation_lifecycle.py` (500-LOC policy,
ADR-2026-09-10-C §7) — shares fixtures/entity-snapshot helpers via
`_reconciliation_test_support.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
    resolve_reconciliation,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from tests.foundation.integration.reconciliation._reconciliation_test_support import (
    connection_repo,
    matching_entities,
    mismatched_entities,
    pool,
    repo,
    risk_repo,
    tenant,
)

__all__ = ["pool", "repo", "connection_repo", "risk_repo"]


async def test_resolve_does_not_reactivate_or_clear_safety_control(
    pool, repo, connection_repo, risk_repo
):
    """REC-007 — resolve alone cannot resume; safety_control은 resolve로
    건드리지 않는다(별도 deactivate_safety_control 호출이 필요)."""
    tenant_id = await tenant(pool)
    target_ref = tenant_id

    await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=mismatched_entities(),
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


async def test_resolve_rejects_missing_target_without_writing(pool, repo, monkeypatch):
    """REC-007 negative: a missing target cannot become RESOLVED."""
    tenant_id = await tenant(pool)
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
    tenant_id = await tenant(pool)
    caller_id = await tenant(pool) if rejection == "cross_tenant" else tenant_id
    entities = mismatched_entities() if rejection == "cross_tenant" else matching_entities()
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
    tenant_id = await tenant(pool)
    await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=tenant_id,
        connection_id=None,
        entities=mismatched_entities(),
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
async def test_resolve_missing_target_rejection_p95_budget(pool, repo, perf_budget):
    """PLT mutation budget: DB-backed rejection alone must fit p95 < 800ms.

    `perf` marker (task-7434 guard): this is a wall-clock budget over DB round
    trips, so it runs in the serial perf stage, not under xdist core contention.

    This measures the command, not the full HTTP route; the route budget is
    defined in L4_platform_observability_tenancy_api_v1.0.md section 7.
    """
    tenant_id = await tenant(pool)

    async def _probe_once() -> None:
        with pytest.raises(ReconciliationStateNotFoundError):
            await resolve_reconciliation(
                repo,
                tenant_id=tenant_id,
                actor_subject_id=tenant_id,
                target_ref=uuid4(),
                reason="rejection latency probe",
            )

    samples = [await perf_budget.sample_async(_probe_once) for _ in range(20)]
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(len(wall_ms_list) * 0.95), len(wall_ms_list) - 1)]
    budget_ms = 800.0
    print(f"[REC-007 resolve rejection] p95={p95_ms:.2f}ms budget<{budget_ms:.0f}ms")
    assert p95_ms < budget_ms, f"resolve rejection p95={p95_ms:.3f}s exceeds 800ms mutation budget"
