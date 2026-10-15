"""BT-5 — Partial fill model (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-5, §3.4(`partial_fill: {max_participation_pct}`).

Orders exceeding the maximum participable volume per bar
(`max_participation_pct * bar_volume`) are filled only up to that limit
on the current bar; the remainder stays as open quantity. Rollover logic
to retry on the next bar is the responsibility of composition (BT-10),
so this module handles only per-bar fill/remaining calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import PartialFillConfig


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name} does not accept negative or NaN values: {value}")


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
    """Fill `order_quantity` within this bar's maximum participable volume
    (`capacity`) and return the remainder as `remaining_quantity`."""

    _reject_negative_or_nan(order_quantity, "order_quantity")
    _reject_negative_or_nan(bar_volume, "bar_volume")

    capacity = bar_volume * config.max_participation_pct
    filled = min(order_quantity, capacity)
    remaining = order_quantity - filled
    return PartialFillOutcome(filled_quantity=filled, remaining_quantity=remaining)
