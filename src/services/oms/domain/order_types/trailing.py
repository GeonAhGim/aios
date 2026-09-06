"""트레일링 스톱 — 단조 트리거 갱신(순수)(L4 명세 §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

`side=SELL`(롱 포지션 청산용)은 체결가가 신고가를 갱신할 때마다 극값을
올리고 트리거가를 `극값 - trailing_offset`으로 끌어올린다. `side=BUY`
(숏 포지션 청산용)는 신저가를 따라 트리거가를 `극값 + trailing_offset`
으로 끌어내린다. 두 경우 모두 트리거가는 불리한 방향으로 절대
되돌리지 않는다(단조) — 시장이 반대로 되돌아가도 이미 확보한 보호
수준을 잃지 않는다. 백테스트 쪽 동형 로직(BT-6 `update_trailing_stop`)은
봉 고저·비율(trail_pct)로 갱신하지만, 여기는 라이브 틱·절대가 오프셋
(trailing_offset)이 계약 필드(`SubmitOrderCommand.trailing_offset`)와
바로 맞물리게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.services.oms.domain.order_types.stop import is_stop_triggered

__all__ = [
    "TrailingStopState",
    "initial_trailing_state",
    "update_trailing_stop",
    "is_trailing_triggered",
]


def _reject_non_positive(value: Decimal, name: str) -> None:
    if value.is_nan() or value <= 0:
        raise ValueError(f"{name}는 0보다 커야 합니다: {value}")


@dataclass(frozen=True, slots=True)
class TrailingStopState:
    extreme_price: Decimal
    trigger_price: Decimal


def initial_trailing_state(
    *, side: OrderSide, reference_price: Decimal, trailing_offset: Decimal
) -> TrailingStopState:
    _reject_non_positive(reference_price, "reference_price")
    _reject_non_positive(trailing_offset, "trailing_offset")
    trigger_price = (
        reference_price - trailing_offset
        if side == OrderSide.SELL
        else reference_price + trailing_offset
    )
    _reject_non_positive(trigger_price, "trigger_price")
    return TrailingStopState(extreme_price=reference_price, trigger_price=trigger_price)


def update_trailing_stop(
    *,
    side: OrderSide,
    state: TrailingStopState,
    last_price: Decimal,
    trailing_offset: Decimal,
) -> TrailingStopState:
    """새 틱을 반영한 다음 상태를 반환한다 — 트리거가는 `max`/`min`으로
    이전 값과 비교해 절대 불리한 방향으로 움직이지 않는다(단조 갱신)."""
    _reject_non_positive(last_price, "last_price")
    _reject_non_positive(trailing_offset, "trailing_offset")
    if side == OrderSide.SELL:
        new_extreme = max(state.extreme_price, last_price)
        new_trigger = max(state.trigger_price, new_extreme - trailing_offset)
    else:
        new_extreme = min(state.extreme_price, last_price)
        new_trigger = min(state.trigger_price, new_extreme + trailing_offset)
    return TrailingStopState(extreme_price=new_extreme, trigger_price=new_trigger)


def is_trailing_triggered(
    *, side: OrderSide, state: TrailingStopState, last_price: Decimal
) -> bool:
    return is_stop_triggered(side=side, trigger_price=state.trigger_price, last_price=last_price)
