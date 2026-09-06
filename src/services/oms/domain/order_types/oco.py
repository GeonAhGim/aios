"""OCO(One-Cancels-Other) 형제 조정 — 원자적 트리거 판정(순수, 스레드
세이프)(L4 명세 §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

두 형태의 판정이 필요하다.
- `resolve_oco`: 결정론적 리플레이(단일 스레드, 같은 틱에서 두 레그가
  동시에 조건을 만족하는 모호한 경우)용 — 호출자가 `priority_leg`로
  보수적 가정을 명시한다. 백테스트 쪽 BT-6 `resolve_oco`와 동형이다.
- `OcoGroup`: 라이브 경로용 — 두 레그의 트리거 평가가 서로 다른
  스레드/코루틴에서 실제로 동시에 도착할 수 있다(가격 틱과 시간 만료가
  같은 순간 도착하는 등). `try_trigger`는 `threading.Lock`으로 "먼저
  도착한 레그가 이긴다"를 원자적으로 강제한다 — 승자가 정해진 뒤에는
  같은 레그를 몇 번 다시 불러도 같은 답을 낸다(멱등). DoD: 1000회 동시
  호출에서 두 레그가 동시에 TRIGGERED가 되는 경우가 0이어야 한다
  (이중 체결 방지).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

OcoLeg = Literal["a", "b"]

__all__ = ["OcoLeg", "OcoOutcome", "OcoResolution", "resolve_oco", "OcoGroup"]


class OcoOutcome(str, Enum):
    TRIGGERED = "TRIGGERED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class OcoResolution:
    triggered_leg: Literal["a", "b", "none"]
    cancelled_leg: Literal["a", "b", "none"]


def resolve_oco(
    *, leg_a_triggered: bool, leg_b_triggered: bool, priority_leg: OcoLeg
) -> OcoResolution:
    """한쪽이 트리거되면 반대편은 즉시 취소된다. 같은 틱 안에서 둘 다
    조건을 만족하는 경우(예: 급격한 갭)는 이 정보만으로 어느 쪽이 실제로
    먼저였는지 알 수 없다 — 이 모호함을 조용히 숨기지 않고, 호출자가
    `priority_leg`로 보수적 가정(예: 손절 레그 우선)을 명시적으로
    고르게 강제한다."""
    if leg_a_triggered and leg_b_triggered:
        if priority_leg == "a":
            return OcoResolution(triggered_leg="a", cancelled_leg="b")
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    if leg_a_triggered:
        return OcoResolution(triggered_leg="a", cancelled_leg="b")
    if leg_b_triggered:
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    return OcoResolution(triggered_leg="none", cancelled_leg="none")


@dataclass
class OcoGroup:
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _winner: OcoLeg | None = field(default=None, repr=False)

    def try_trigger(self, leg: OcoLeg) -> OcoOutcome:
        """이 레그를 트리거로 확정 시도한다. 그룹이 아직 안 정해졌으면
        이 레그가 승자가 되고 TRIGGERED를 반환한다. 이미 정해졌으면
        승자와 같은 레그는 TRIGGERED(멱등 재확인), 다른 레그는 항상
        CANCELLED를 반환한다 — lock 보유 구간이 read-modify-write 전체를
        감싸므로 두 스레드가 동시에 들어와도 승자는 정확히 하나다."""
        with self._lock:
            if self._winner is None:
                self._winner = leg
            return OcoOutcome.TRIGGERED if self._winner == leg else OcoOutcome.CANCELLED

    @property
    def winner(self) -> OcoLeg | None:
        with self._lock:
            return self._winner
