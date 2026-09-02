"""FND-06 Risk & Safety Gate 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from uuid import uuid4

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
