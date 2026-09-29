"""FND-08 Reconciliation lifecycle test (L4_*#FND-08, REC-001~004/006, no scheduler).

`resolve_reconciliation` (REC-007) negative/adversarial/perf cases live in
`test_reconciliation_resolve.py` (500-LOC policy, ADR-2026-09-10-C §7) — both
files share fixtures/entity-snapshot helpers via `_reconciliation_test_support.py`.
"""

from __future__ import annotations

from uuid import uuid4

from src.foundation.connections.domain.models import AccountConnection, CapabilityScope
from src.foundation.connections.domain.models import ConnectionState as ConnState
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from tests.foundation.integration.reconciliation._reconciliation_test_support import (
    connection_repo,
    matching_entities,
    minor_difference_entities,
    mismatched_entities,
    pool,
    repo,
    risk_repo,
    tenant,
)

__all__ = ["pool", "repo", "connection_repo", "risk_repo"]


async def test_matching_values_creates_healthy_run(pool, repo, connection_repo, risk_repo):
    """REC-001 — matching order/fill/position/cash creates HEALTHY
    evidence/projection."""
    tenant_id = await tenant(pool)
    target_ref = tenant_id  # 별도 deployment 없이 tenant 자체를 target으로 재사용

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=matching_entities(),
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
    tenant_id = await tenant(pool)
    target_ref = tenant_id

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=mismatched_entities(),
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
    tenant_id = await tenant(pool)
    target_ref = tenant_id

    first = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=mismatched_entities(),
    )
    second = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=mismatched_entities(),
    )
    assert first.id == second.id

    first_state = await repo.get_state(target_ref)
    # 두 번째 호출이 dedup됐다면(같은 run 반환) upsert_state도 다시
    # 호출되지 않아야 한다 — revision이 여전히 0이면 안전 통제 활성화도
    # 딱 한 번뿐이었다는 뜻(activate_safety_control은 매번 새 control
    # 행을 만들므로, 두 번 불렸다면 fence_token이 2 이상이었을 것).
    control = await risk_repo.get_safety_control(first_state.safety_control_id)
    assert control.fence_token == 1


async def test_minor_difference_does_not_activate_safety_control(
    pool, repo, connection_repo, risk_repo
):
    """F3 case 1 — a within-tolerance gap classifies MINOR_DIFFERENCE and must
    not trip the kill switch: MINOR_DIFFERENCE sits outside
    `_BLOCKING_CLASSIFICATIONS` (only MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE
    activate a safety control, run_reconciliation.py)."""
    tenant_id = await tenant(pool)
    target_ref = tenant_id

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=minor_difference_entities(),
    )
    assert run.aggregate_classification.value == "MINOR_DIFFERENCE"

    state = await repo.get_state(target_ref)
    assert state.aggregate_status.value == "MINOR_DIFFERENCE"
    assert state.safety_control_id is None
    assert state.blocking_reason is None


async def test_minor_difference_worsening_to_material_mismatch_activates_control(
    pool, repo, connection_repo, risk_repo
):
    """F3 case 2 — a target already at MINOR_DIFFERENCE that later widens past
    tolerance must transition to MATERIAL_MISMATCH and newly activate a
    safety control (it had none before)."""
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
        entities=minor_difference_entities(),
    )
    before = await repo.get_state(target_ref)
    assert before.safety_control_id is None

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=mismatched_entities(),
    )
    assert run.aggregate_classification.value == "MATERIAL_MISMATCH"

    after = await repo.get_state(target_ref)
    assert after.aggregate_status.value == "MATERIAL_MISMATCH"
    assert after.safety_control_id is not None
    assert after.blocking_reason is not None

    control = await risk_repo.get_safety_control(after.safety_control_id)
    assert control is not None
    assert control.state.value == "ACTIVE"


async def test_material_mismatch_easing_to_minor_difference_keeps_control_active(
    pool, repo, connection_repo, risk_repo
):
    """F3 case 3 — a target easing back from MATERIAL_MISMATCH into tolerance
    updates `aggregate_status` to MINOR_DIFFERENCE, but the previously
    activated safety control must stay ACTIVE and attached
    (`upsert_state()`'s COALESCE keeps the old `safety_control_id` since the
    new run activates none) — only `evaluate_recovery` (not covered here)
    is allowed to clear it."""
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
    before = await repo.get_state(target_ref)
    assert before.safety_control_id is not None
    control_before = await risk_repo.get_safety_control(before.safety_control_id)
    assert control_before.state.value == "ACTIVE"

    run = await run_reconciliation(
        repo,
        connection_repo,
        risk_repo,
        tenant_id=tenant_id,
        target_type="PAPER_DEPLOYMENT",
        target_ref=target_ref,
        connection_id=None,
        entities=minor_difference_entities(),
    )
    assert run.aggregate_classification.value == "MINOR_DIFFERENCE"

    after = await repo.get_state(target_ref)
    assert after.aggregate_status.value == "MINOR_DIFFERENCE"
    assert after.safety_control_id == before.safety_control_id

    control_after = await risk_repo.get_safety_control(after.safety_control_id)
    assert control_after.state.value == "ACTIVE"


async def test_connection_unavailable_marks_all_items_unavailable(
    pool, repo, connection_repo, risk_repo
):
    """REC-003 — provider timeout/partial payload marks unavailable, no
    zero-state assumption. connection이 아예 없으면(get_latest_health가
    None) unhealthy로 취급한다."""
    tenant_id = await tenant(pool)
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
        entities=matching_entities(),
    )
    assert run.aggregate_classification.value == "PROVIDER_UNAVAILABLE"
    assert all(item.classification.value == "PROVIDER_UNAVAILABLE" for item in run.items)
