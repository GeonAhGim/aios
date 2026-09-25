"""FND-06 Risk & Safety Gate 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import (
    RiskEvaluationInput,
    RiskOutcome,
    SafetyControl,
    SafetyControlState,
    SafetyScope,
)
from src.foundation.risk_gate.domain.rules import (
    compose_safety_controls,
    compute_subject_fingerprint,
    evaluate_risk,
)
from tests.conftest import PerfBudget


def _control(scope: SafetyScope, state=SafetyControlState.ACTIVE) -> SafetyControl:
    return SafetyControl(
        id=uuid4(),
        scope=scope,
        scope_ref="",
        state=state,
        reason="test",
        actor_subject_id=uuid4(),
        fence_token=1,
    )


def test_compose_safety_controls_none_active_returns_none():
    outcome, reasons = compose_safety_controls(())
    assert outcome is None
    assert reasons == []


def test_compose_safety_controls_ignores_inactive():
    outcome, reasons = compose_safety_controls(
        (_control(SafetyScope.TENANT, state=SafetyControlState.INACTIVE),)
    )
    assert outcome is None
    assert reasons == []


def test_compose_safety_controls_active_denies():
    outcome, reasons = compose_safety_controls((_control(SafetyScope.ACCOUNT),))
    assert outcome == RiskOutcome.DENY
    assert reasons == ["RISK_KILL_SWITCH_ACTIVE_ACCOUNT"]


def test_compose_safety_controls_orders_by_severity():
    """48번 §5 acceptance test 4 — global이 가장 앞선 이유로 나온다."""
    outcome, reasons = compose_safety_controls(
        (_control(SafetyScope.ACCOUNT), _control(SafetyScope.GLOBAL), _control(SafetyScope.TENANT))
    )
    assert outcome == RiskOutcome.DENY
    assert reasons == [
        "RISK_KILL_SWITCH_ACTIVE_GLOBAL",
        "RISK_KILL_SWITCH_ACTIVE_TENANT",
        "RISK_KILL_SWITCH_ACTIVE_ACCOUNT",
    ]


def test_evaluate_risk_kill_switch_wins_over_everything():
    """48번 §5 acceptance test 1/3 — kill switch가 최우선."""
    result = evaluate_risk(
        RiskEvaluationInput(
            mandate_available=True,
            mandate_blocking=False,
            connection_fresh=True,
            active_controls=(_control(SafetyScope.GLOBAL),),
        )
    )
    assert result[0] == RiskOutcome.DENY
    assert "RISK_KILL_SWITCH_ACTIVE_GLOBAL" in result[1]


def test_evaluate_risk_missing_mandate_denies_not_implicit_allow():
    """RSK-002 — missing input never implicitly allows."""
    outcome, reasons, _ = evaluate_risk(RiskEvaluationInput(mandate_available=False))
    assert outcome == RiskOutcome.DENY
    assert reasons == ["RISK_INPUT_MANDATE_MISSING"]


def test_evaluate_risk_mandate_blocking_denies():
    outcome, reasons, _ = evaluate_risk(
        RiskEvaluationInput(
            mandate_available=True,
            mandate_blocking=True,
            mandate_reason_codes=("POLICY_MAX_TOTAL_EXPOSURE",),
        )
    )
    assert outcome == RiskOutcome.DENY
    assert reasons == ["POLICY_MAX_TOTAL_EXPOSURE"]


def test_evaluate_risk_stale_connection_pauses():
    outcome, reasons, obligations = evaluate_risk(
        RiskEvaluationInput(mandate_available=True, mandate_blocking=False, connection_fresh=False)
    )
    assert outcome == RiskOutcome.PAUSE
    assert reasons == ["RISK_INPUT_STALE"]
    assert obligations == ["REQUIRE_FRESH_CONNECTION"]


def test_evaluate_risk_allows_when_everything_clear():
    outcome, reasons, obligations = evaluate_risk(
        RiskEvaluationInput(mandate_available=True, mandate_blocking=False, connection_fresh=True)
    )
    assert outcome == RiskOutcome.ALLOW
    assert reasons == []
    assert obligations == []


def test_evaluate_risk_connection_fresh_none_is_not_checked():
    """connection_id를 지정하지 않은 평가(예: connection이 아직 없는 최초
    mandate 평가)는 freshness 검사 자체를 건너뛴다."""
    outcome, _, _ = evaluate_risk(
        RiskEvaluationInput(mandate_available=True, mandate_blocking=False, connection_fresh=None)
    )
    assert outcome == RiskOutcome.ALLOW


def test_fingerprint_is_stable_for_same_input():
    """RSK-001 — pinned input/rule produces stable decision/fingerprint."""
    a = compute_subject_fingerprint("tenant-1", "DEPLOYMENT", "payload")
    b = compute_subject_fingerprint("tenant-1", "DEPLOYMENT", "payload")
    assert a == b


def test_fingerprint_differs_for_different_gate_kind():
    a = compute_subject_fingerprint("tenant-1", "DEPLOYMENT", "payload")
    b = compute_subject_fingerprint("tenant-1", "PRE_INTENT", "payload")
    assert a != b


def test_compose_safety_controls_duplicate_scope_keeps_a_single_deterministic_reason() -> None:
    """`active_by_scope = {c.scope: c for c in active}`는 dict 키가
    scope라 같은 scope의 control이 여러 개 있어도 한 항목으로 합쳐진다 —
    reason 코드가 scope당 정확히 하나만 나와야 한다(중복 reason은 감사
    로그를 흐트러뜨린다)."""
    first = _control(SafetyScope.ACCOUNT)
    second = _control(SafetyScope.ACCOUNT)

    outcome, reasons = compose_safety_controls((first, second))

    assert outcome == RiskOutcome.DENY
    assert reasons == ["RISK_KILL_SWITCH_ACTIVE_ACCOUNT"]
    assert len(reasons) == 1


@pytest.mark.perf
def test_compute_subject_fingerprint_meets_latency_budget_over_many_calls(
    perf_budget: PerfBudget,
) -> None:
    """캐시 재사용 경로(RSK-001)가 fingerprint 계산 자체의 비용으로 막히지
    않아야 한다."""
    iterations = 20_000
    budget_ms = 1000.0

    def _run() -> None:
        for _ in range(iterations):
            compute_subject_fingerprint("tenant-1", "DEPLOYMENT", "payload")

    perf_budget.assert_within(
        _run,
        budget_ms=budget_ms,
        label=f"compute_subject_fingerprint() x{iterations}",
    )


@pytest.mark.perf
def test_evaluate_risk_meets_latency_budget_over_many_calls(perf_budget: PerfBudget) -> None:
    """ALLOW 경로(가장 흔한 호출)의 결정 트리 평가가 절대시간 예산 내에
    있어야 한다 — risk gate는 매 intent/deployment 평가마다 호출되는 hot
    path다."""
    iterations = 20_000
    budget_ms = 1000.0
    gate_input = RiskEvaluationInput(
        mandate_available=True, mandate_blocking=False, connection_fresh=True
    )

    def _run() -> None:
        for _ in range(iterations):
            evaluate_risk(gate_input)

    perf_budget.assert_within(
        _run,
        budget_ms=budget_ms,
        label=f"evaluate_risk() x{iterations}",
    )
