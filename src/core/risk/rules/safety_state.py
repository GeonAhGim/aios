"""L4_risk_and_safety_v1.0.md#2.1, §9 R-13 — 안전 상태 5입력 판정.

CB level·활성 kill-switch control·데이터 불신 레벨·안전계층에 의한 실행
일시정지·연결 신선도, 5가지 입력을 각각 차단한다. §5 평가 순서상 이 규칙이
가장 먼저 평가되지만 단락(short-circuit) 자체는 R-16 `evaluator.py`의
책임이라 이 함수는 판정 결과 하나만 반환한다(decision 각주). 순수(I/O 금지),
상한 90줄.
"""
from __future__ import annotations

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing

_RULE_ID = "safety_state"
# CircuitBreakerLevel 값은 소문자(src/core/safety/circuit_breaker.py) — DB CHECK와 동일.
_CB_DENY_LEVELS = ("restricted", "halted", "emergency")
# DataDistrustLevel 값은 대문자(src/core/safety/data_distrust.py).
_DISTRUST_DENY_LEVELS = ("SUSPICIOUS", "DISTRUSTED")


def safety_state(inputs: RiskInputs, _policy: RiskPolicy) -> RuleResult:
    safety = inputs.safety

    if safety.circuit_breaker_level is None:
        return missing(_RULE_ID, "safety.circuit_breaker_level", unit="count")
    level = safety.circuit_breaker_level
    if level in _CB_DENY_LEVELS:
        return _deny(f"RISK_CIRCUIT_BREAKER_{level.upper()}")
    if level == "warning":
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.ESCALATE,
            reason_code="RISK_CIRCUIT_BREAKER_WARNING",
            unit="count",
        )

    if safety.active_control_scopes is None:
        return missing(_RULE_ID, "safety.active_control_scopes", unit="count")
    if safety.active_control_scopes:
        return _deny(f"RISK_KILL_SWITCH_ACTIVE_{safety.active_control_scopes[0]}")

    if safety.data_distrust_level is None:
        return missing(_RULE_ID, "safety.data_distrust_level", unit="count")
    if safety.data_distrust_level in _DISTRUST_DENY_LEVELS and not inputs.intent.reduce_only:
        return _deny(f"RISK_DATA_DISTRUST_{safety.data_distrust_level}")

    if safety.execution_paused_by_safety is None:
        return missing(_RULE_ID, "safety.execution_paused_by_safety", unit="count")
    if safety.execution_paused_by_safety:
        return _deny("RISK_EXECUTION_PAUSED_BY_SAFETY")

    if safety.connection_fresh is None:
        return missing(_RULE_ID, "safety.connection_fresh", unit="count")
    if not safety.connection_fresh:
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.PAUSE,
            reason_code="RISK_INPUT_STALE",
            unit="count",
        )

    return RuleResult(rule_id=_RULE_ID, outcome=RiskOutcome.ALLOW, unit="count")


def _deny(reason_code: str) -> RuleResult:
    return RuleResult(
        rule_id=_RULE_ID, outcome=RiskOutcome.DENY, reason_code=reason_code, unit="count"
    )
