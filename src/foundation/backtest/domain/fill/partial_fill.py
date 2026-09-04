"""BT-5 — 부분체결 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-5, §3.4(`partial_fill: {max_participation_pct}`).

한 봉이 소화할 수 있는 최대 참여율(`max_participation_pct * bar_volume`)을
넘는 주문은 그 봉에서 그만큼만 체결되고 나머지는 잔량으로 남는다 — 잔량을
다음 봉에 재시도하는 이월 로직은 조립(BT-10)의 책임이라 여기서는 한
봉의 체결/잔량 계산만 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import PartialFillConfig


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


@dataclass(frozen=True, slots=True)
class PartialFillOutcome:
    filled_quantity: Decimal
    remaining_quantity: Decimal

    @property
    def is_fully_filled(self) -> bool:
        return self.remaining_quantity == 0


def compute_partial_fill(
    config: PartialFillConfig, *, order_quantity: Decimal, bar_volume: Decimal
) -> PartialFillOutcome:
    """`order_quantity`를 이 봉의 최대 참여 물량(`capacity`) 안에서
    체결시키고 나머지를 `remaining_quantity`로 돌려준다."""

    _reject_negative_or_nan(order_quantity, "order_quantity")
    _reject_negative_or_nan(bar_volume, "bar_volume")

    capacity = bar_volume * config.max_participation_pct
    filled = min(order_quantity, capacity)
    remaining = order_quantity - filled
    return PartialFillOutcome(filled_quantity=filled, remaining_quantity=remaining)
