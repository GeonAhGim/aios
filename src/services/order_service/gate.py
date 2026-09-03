"""주문 제출 전 위험 게이트 — 순수 타입만 정의한다.

이 모듈은 의도적으로 `src/foundation/*`를 import하지 않는다 — `submit.py`가
foundation을 직접 알면 legacy 실행 경로(SCAFFOLD)와 새 Foundation 컨텍스트
사이에 원치 않는 결합이 생긴다(PM 지침, 2026-09-03 mandate/kill-switch
배선 작업). 실제 foundation 호출은 `foundation_gate.py`(이 모듈과 별개 —
foundation을 아는 쪽)가 구현하고, 조립부(scheduler)가 그 구현체를
`submit_order(pre_submit_gate=...)`로 주입한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from src.core.observability.metric_names import (
    RISK_DECISION_COUNT_TOTAL,
    RISK_EVALUATION_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort


class GateOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class OrderContext:
    user_id: UUID
    execution_id: int | None
    exchange: str
    mandate_revision_id: UUID | None


@dataclass(frozen=True)
class GateDecision:
    outcome: GateOutcome
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


PreSubmitGate = Callable[[OrderContext], Awaitable[GateDecision]]


def record_gate_decision(
    metrics: MetricsPort,
    decision: GateDecision,
    *,
    duration_seconds: float,
    engine: str = "core",
) -> None:
    """§7.2 `aios.risk.decision.count_total`/`aios.risk.evaluation.duration_seconds`.

    이 모듈은 foundation을 모르는 순수 타입 모듈이라는 원칙(위 docstring) 때문에
    게이트 실행 자체가 아니라 그 결과를 소비하는 호출부(`submit.py`)가 이
    함수로 계측한다.
    """
    reason_code = decision.reason_codes[0] if decision.reason_codes else "none"
    metrics.counter(
        RISK_DECISION_COUNT_TOTAL,
        labels={"engine": engine, "effect": decision.outcome.value, "reason_code": reason_code},
    )
    metrics.observe(RISK_EVALUATION_DURATION_SECONDS, duration_seconds, labels={"engine": engine})
