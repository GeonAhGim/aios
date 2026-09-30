"""R-35 `evaluate_pre_submit` 통합테스트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-35.
DoD: (1) 4개 입력(control/CB/distrust/connection) 각각 단독 DENY·PAUSE +
None 입력 fail-closed DENY(I2). (2) TTL 2s 정확 + is_actionable. (3) F0가
control 조회와 같은 스냅샷에서 읽힌 값(5쌍 모두 포함). (4) 교차 tenant
격리. (5) recorder(R-25)로 WORM 기록(DENY 포함).

공유 fixture/fake(`pool`, `risk_repo`, `signal_repo`, ...)는
`conftest.py`에 있다 — 동시성(D3) 시나리오는
`test_pre_submit_gate_concurrency.py`, RECON_MISMATCH 심볼단위 DENY(task
-9224)는 `test_pre_submit_gate_recon_mismatch.py`로 분리했다(loc_over_500
분리, CLAUDE.md §7).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from src.core.risk.decision import GateKind, RiskOutcome
from src.foundation.connections.domain.models import HealthState
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import SafetyScope
from tests.integration.conftest import create_test_tenant
from tests.integration.risk.conftest import (
    PROVIDER,
    QTY,
    SIDE,
    SYMBOL,
    FakeConnectionRepo,
    NoConnectionRepo,
    RiskRepoWithFixedSafetyState,
    healthy_connection_repo,
    normal_risk_repo,
)


async def test_baseline_allow_and_ttl_is_exactly_two_seconds(
    pool, risk_repo, signal_repo, recorder
):
    tenant_id = await create_test_tenant(pool)
    execution_ref = f"exec:{uuid4().hex[:8]}"
    decision, fence = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW
    assert decision.gate_kind == GateKind.PRE_SUBMIT
    assert decision.expires_at == decision.evaluated_at + timedelta(seconds=2)
    assert decision.is_actionable(decision.evaluated_at) is True
    assert decision.is_actionable(decision.evaluated_at + timedelta(seconds=2.1)) is False
    assert set(fence.tokens) == set(fence_pairs_for(tenant_id, PROVIDER, execution_ref))


async def test_active_control_alone_denies_and_fence_matches_same_snapshot(
    pool, risk_repo, signal_repo, recorder
):
    tenant_id = await create_test_tenant(pool)
    control = await risk_repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="pre-submit control test",
        actor_subject_id=tenant_id,
    )

    decision, fence = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_KILL_SWITCH_ACTIVE_ACCOUNT" in decision.reason_codes
    # DoD(3) — F0는 이 control 판단과 같은 스냅샷에서 읽힌 값이어야 하므로,
    # 이 control이 만든 fence 토큰과 정확히 일치해야 한다.
    assert fence.tokens[(SafetyScope.ACCOUNT, str(tenant_id))] == control.fence_token


async def test_circuit_breaker_alone_denies(pool, risk_repo, signal_repo, recorder):
    tenant_id = await create_test_tenant(pool)
    repo = RiskRepoWithFixedSafetyState(risk_repo, cb_level="halted", distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_CIRCUIT_BREAKER_HALTED" in decision.reason_codes


async def test_data_distrust_alone_denies(pool, risk_repo, signal_repo, recorder):
    tenant_id = await create_test_tenant(pool)
    repo = RiskRepoWithFixedSafetyState(risk_repo, cb_level="normal", distrust_level="DISTRUSTED")

    decision, _ = await evaluate_pre_submit(
        repo,
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_DATA_DISTRUST_DISTRUSTED" in decision.reason_codes


async def test_connection_stale_alone_pauses(pool, risk_repo, signal_repo, recorder):
    tenant_id = await create_test_tenant(pool)
    stale_connection = FakeConnectionRepo(
        tenant_id=tenant_id, provider_code=PROVIDER, health=HealthState.DEGRADED
    )

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        stale_connection,
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.PAUSE
    assert "RISK_INPUT_STALE" in decision.reason_codes


async def test_missing_circuit_breaker_level_is_fail_closed_deny(
    pool, risk_repo, signal_repo, recorder
):
    """I2 negative test — None을 '문제없음'으로 읽지 않는다."""
    tenant_id = await create_test_tenant(pool)
    repo = RiskRepoWithFixedSafetyState(risk_repo, cb_level=None, distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_INPUT_MISSING:cb_level" in decision.reason_codes


async def test_missing_connection_is_fail_closed_deny(pool, risk_repo, signal_repo, recorder):
    """I2 negative test — 이 provider에 connection 자체가 없으면 '건강함'이
    아니라 결손으로 취급해 DENY한다."""
    tenant_id = await create_test_tenant(pool)

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        NoConnectionRepo(),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_INPUT_MISSING:connection_fresh" in decision.reason_codes


async def test_other_tenants_control_does_not_leak_into_this_tenants_decision(
    pool, risk_repo, signal_repo, recorder
):
    tenant_id = await create_test_tenant(pool)
    other_tenant_id = await create_test_tenant(pool)
    await risk_repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(other_tenant_id),
        reason="other tenant control",
        actor_subject_id=other_tenant_id,
    )

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW


async def test_denied_decision_is_recorded_in_worm_table(
    pool, risk_repo, signal_repo, recorder, decision_repo
):
    """DoD(5) — 거부도 recorder(R-25)로 WORM 기록된다."""
    tenant_id = await create_test_tenant(pool)
    repo = RiskRepoWithFixedSafetyState(risk_repo, cb_level="emergency", distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.DENY

    stored = await decision_repo.get(decision.decision_id)
    assert stored is not None
    stored_decision, inputs_snapshot = stored
    assert stored_decision.outcome == RiskOutcome.DENY
    assert stored_decision.gate_kind == GateKind.PRE_SUBMIT
    assert inputs_snapshot["circuit_breaker_level"] == "emergency"
    # task-1532 I10 binding keys are recorded next to the fence (fenced_submit compares them)
    assert (inputs_snapshot["symbol"], inputs_snapshot["side"]) == (SYMBOL, SIDE)
    assert Decimal(inputs_snapshot["quantity"]) == QTY
    pairs = fence_pairs_for(tenant_id, PROVIDER, decision.execution_ref)
    assert set(inputs_snapshot["fence_snapshot"]) == {f"{s.value}:{ref}" for s, ref in pairs}


async def test_fence_snapshot_covers_exactly_the_five_pairs(pool, risk_repo, signal_repo, recorder):
    """DoD(3) — R-33 `fence_pairs_for`를 재구현하지 않고 그대로 5쌍 확인."""
    tenant_id = await create_test_tenant(pool)
    execution_ref = f"exec:{uuid4().hex[:8]}"

    _, fence = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        provider_code=PROVIDER,
        symbol=SYMBOL,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    expected = fence_pairs_for(tenant_id, PROVIDER, execution_ref)
    assert set(fence.tokens) == set(expected)
