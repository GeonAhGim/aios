"""task-1762 — `circuit_breaker.py`/`recovery_gate.py`(0 적대적 테스트)의
"입력 결측(None) → fail-closed" 계약을 증명한다.

두 모듈 모두 순수 함수라 DB가 필요 없다. `data_delay_sec=None`은 "관측
0건(모름)"을 뜻하고(각 모듈 상단 docstring), 이를 "지연 없음"으로 읽으면
fail-open이 된다 — `_exceeds_or_unknown`/`_is_baseline`가 그 결함을
막는다는 것이 이 파일의 대상 불변식이다.
"""
from __future__ import annotations

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.decision import RiskOutcome
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    compute_level,
)
from src.core.safety.recovery_gate import can_reactivate

_COOLDOWN_SEC = 3


def test_circuit_breaker_treats_missing_data_delay_as_halted() -> None:
    """negative — data_delay_sec=None(모름)은 halted 임계 초과로 취급돼야
    한다. 나머지 지표는 전부 정상이라 이 필드 하나만 무너뜨린 결과다."""
    policy = load_risk_policy().circuit_breaker
    metrics = CircuitBreakerMetrics(data_delay_sec=None)

    assert compute_level(metrics, policy) == CircuitBreakerLevel.HALTED


def test_recovery_gate_denies_reactivation_when_history_sample_missing_data_delay() -> None:
    """negative — cooldown 이력 표본 중 단 하나라도 data_delay_sec=None이면
    "모름"을 baseline으로 인정하지 않고 재가동을 거부해야 한다."""
    history = [CircuitBreakerMetrics() for _ in range(_COOLDOWN_SEC)]
    history[-1] = CircuitBreakerMetrics(data_delay_sec=None)

    decision = can_reactivate(
        current_level=CircuitBreakerLevel.HALTED,
        metrics_history=history,
        cooldown_sec=_COOLDOWN_SEC,
        evidence_ref="evidence://task-1762",
        approval_status="APPROVED",
        fresh_risk_outcome=RiskOutcome.ALLOW,
    )

    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"
