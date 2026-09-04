"""L4_risk_and_safety_v1.0.md#4.3(CB 상태 전이표), §9 R-44 — 재가동 순수 규칙.

정책문서 8.6-B·불변식 I5: halted/emergency는 자동 하향 불가하고, 재가동은
`evidence_ref 존재 ∧ metrics 이력이 cooldown 동안 warning 미만 ∧ RECOVERY
결정 ALLOW ∧ 승인 TTL 이내`(§4.3 CB 표 행 4)를 전부 만족해야 한다.

이 함수는 순수 규칙이다 — I/O·DB·시계를 직접 호출하지 않는다. 시계가
필요한 판단(승인 TTL 만료 여부)은 이미 상태로 해소된 `approval_status`
문자열(호출자가 `now`로 미리 계산한 `ApprovalRequest.status`, 예:
"APPROVED"/"EXPIRED")로 주입받는다 — 여기서 다시 시계를 보지 않는다.

`metrics_history`는 정책 임계치(policy)를 받지 않고도 "warning 미만"을
판정해야 하므로, 어떤 정책 설정에서도 항상 안전한 유일한 기준 —
"이력 전체가 완전히 기준값(0)" — 을 쓴다(모든 CB 지표는 0이 상한이며
0보다 큰 임계치를 가지므로 0은 항상 모든 정책의 warning 미만이다). 이력
길이는 `cooldown_sec`와 1:1 대응한다는 계약을 호출자(R-45 배선)에게
지운다 — 매 초 1개 표본을 이어붙이는 것이 그 계약이다.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, model_validator

from src.core.risk.decision import RiskOutcome
from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerMetrics

_REACTIVATABLE = (CircuitBreakerLevel.HALTED, CircuitBreakerLevel.EMERGENCY)
_ZERO = Decimal("0")
_METRIC_FIELDS = (
    "api_error_rate_pct",
    "data_delay_sec",
    "order_reject_rate_pct",
    "daily_loss_pct",
    "api_disconnect_sec",
)


class RecoveryDecision(BaseModel, frozen=True):
    outcome: RiskOutcome
    reason_code: str | None = None

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> RecoveryDecision:
        # I2와 동일한 fail-closed 계약 — DENY는 사유 없이 존재할 수 없다.
        if self.outcome == RiskOutcome.DENY and self.reason_code is None:
            raise ValueError("DENY는 reason_code가 필요하다")
        if self.outcome == RiskOutcome.ALLOW and self.reason_code is not None:
            raise ValueError("ALLOW는 reason_code를 가질 수 없다")
        return self


def _deny(reason_code: str) -> RecoveryDecision:
    return RecoveryDecision(outcome=RiskOutcome.DENY, reason_code=reason_code)


def _is_baseline(metrics: CircuitBreakerMetrics) -> bool:
    return all(getattr(metrics, field) <= _ZERO for field in _METRIC_FIELDS)


def can_reactivate(
    *,
    current_level: CircuitBreakerLevel,
    metrics_history: Sequence[CircuitBreakerMetrics],
    cooldown_sec: int,
    evidence_ref: str | None,
    approval_status: str,
    fresh_risk_outcome: RiskOutcome,
) -> RecoveryDecision:
    """§4.3 CB 표 행 4 — 4가지 조건을 전부 만족해야 ALLOW, 그 외 전부 DENY."""
    if current_level not in _REACTIVATABLE:
        return _deny("RECOVERY_LEVEL_NOT_DEGRADED")
    if not evidence_ref:
        return _deny("RECOVERY_EVIDENCE_MISSING")
    if cooldown_sec <= 0:
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if len(metrics_history) < cooldown_sec:
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if not all(_is_baseline(m) for m in metrics_history):
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if approval_status != "APPROVED":
        return _deny("RECOVERY_APPROVAL_NOT_APPROVED")
    if fresh_risk_outcome != RiskOutcome.ALLOW:
        return _deny("RECOVERY_FRESH_RISK_DENY")
    return RecoveryDecision(outcome=RiskOutcome.ALLOW)
