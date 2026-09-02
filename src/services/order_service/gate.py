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
