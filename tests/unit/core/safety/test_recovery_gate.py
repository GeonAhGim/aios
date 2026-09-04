"""L4_risk_and_safety_v1.0.md#9 R-44 — recovery_gate.can_reactivate 단위 테스트.

§8 "단위(안전)" 필수 negative: evidence 없음 / cooldown 미달 / approval
만료·미승인 / fresh DENY 각각 거부. 나머지 조건은 전부 통과하는 baseline을
만들어 두고 한 번에 하나씩만 무너뜨려 각 거부 사유를 독립적으로 검증한다.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.risk.decision import RiskOutcome
from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerMetrics
from src.core.safety.recovery_gate import can_reactivate

COOLDOWN_SEC = 3


def _clean_history(n: int = COOLDOWN_SEC) -> list[CircuitBreakerMetrics]:
    return [CircuitBreakerMetrics() for _ in range(n)]


def _base_kwargs() -> dict:
    return dict(
        current_level=CircuitBreakerLevel.HALTED,
        metrics_history=_clean_history(),
        cooldown_sec=COOLDOWN_SEC,
        evidence_ref="evidence://ref-1",
        approval_status="APPROVED",
        fresh_risk_outcome=RiskOutcome.ALLOW,
    )


def test_all_conditions_met_allows() -> None:
    decision = can_reactivate(**_base_kwargs())
    assert decision.outcome == RiskOutcome.ALLOW
    assert decision.reason_code is None


@pytest.mark.parametrize("evidence_ref", [None, ""])
def test_missing_evidence_denies(evidence_ref: str | None) -> None:
    kwargs = _base_kwargs()
    kwargs["evidence_ref"] = evidence_ref
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_EVIDENCE_MISSING"


def test_cooldown_not_met_by_short_history_denies() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = _clean_history(COOLDOWN_SEC - 1)
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


def test_cooldown_not_met_by_degraded_sample_denies() -> None:
    kwargs = _base_kwargs()
    history = _clean_history()
    history[-1] = CircuitBreakerMetrics(api_error_rate_pct=Decimal("0.01"))
    kwargs["metrics_history"] = history
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


def test_cooldown_sec_not_positive_denies() -> None:
    kwargs = _base_kwargs()
    kwargs["cooldown_sec"] = 0
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


@pytest.mark.parametrize(
    "approval_status", ["EXPIRED", "PENDING", "REJECTED", "CANCELLED", "unknown"]
)
def test_approval_not_approved_denies(approval_status: str) -> None:
    kwargs = _base_kwargs()
    kwargs["approval_status"] = approval_status
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_APPROVAL_NOT_APPROVED"


@pytest.mark.parametrize(
    "outcome",
    [RiskOutcome.DENY, RiskOutcome.REDUCE, RiskOutcome.PAUSE, RiskOutcome.ESCALATE],
)
def test_fresh_risk_outcome_not_allow_denies(outcome: RiskOutcome) -> None:
    kwargs = _base_kwargs()
    kwargs["fresh_risk_outcome"] = outcome
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_FRESH_RISK_DENY"


@pytest.mark.parametrize(
    "current_level",
    [
        CircuitBreakerLevel.NORMAL,
        CircuitBreakerLevel.WARNING,
        CircuitBreakerLevel.RESTRICTED,
    ],
)
def test_non_degraded_level_denies(current_level: CircuitBreakerLevel) -> None:
    kwargs = _base_kwargs()
    kwargs["current_level"] = current_level
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_LEVEL_NOT_DEGRADED"


def test_exact_cooldown_boundary_allows_when_all_clean() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = _clean_history(COOLDOWN_SEC)
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.ALLOW


def test_empty_history_never_allows_regardless_of_cooldown() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = []
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"
