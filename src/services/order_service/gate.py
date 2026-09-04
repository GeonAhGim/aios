"""주문 제출 전 위험 게이트 — 순수 타입만 정의한다.

이 모듈은 의도적으로 `src/foundation/*`를 import하지 않는다 — `submit.py`가
foundation을 직접 알면 legacy 실행 경로(SCAFFOLD)와 새 Foundation 컨텍스트
사이에 원치 않는 결합이 생긴다(PM 지침, 2026-09-03 mandate/kill-switch
배선 작업). 실제 foundation 호출은 `foundation_gate.py`(이 모듈과 별개 —
foundation을 아는 쪽)가 구현하고, 조립부(scheduler)가 그 구현체를
`submit_order(pre_submit_gate=...)`로 주입한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
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
    # R-36 — R-33 fence(F0)를 PRE_TRADE 등 이전 평가 시점에 관측했다면 여기
    # 담아 넘긴다. 키는 `"{SafetyScope.value}:{scope_ref}"`(foundation_gate.py가
    # 채운다 — 이 모듈은 foundation을 몰라 SafetyScope를 직접 쓰지 않는다).
    # None(기본값)이면 신선도 비교를 건너뛴다 — 지금까지 이 값을 관측해 둔
    # 호출부가 없다(마이그레이션 대기, mandate_revision_id와 동급 상태).
    observed_fence: Mapping[str, int] | None = None


@dataclass(frozen=True)
class GateDecision:
    outcome: GateOutcome
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    # R-36 — 이 결정이 근거한 fence 스냅샷(F0). 항상 채워진다(foundation_gate가
    # 매 평가마다 함께 읽는다) — 다음 단계(R-37 fenced_submit)가 실제 쓰기
    # 직전 재조회한 값과 비교해 stale이면 거부할 수 있게 그대로 넘겨준다.
    fence_snapshot: Mapping[str, int] = field(default_factory=dict)


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
