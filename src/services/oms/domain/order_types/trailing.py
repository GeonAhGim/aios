"""Trailing stop — monotonic trigger update (pure) (L4 spec §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

`side=SELL` (for closing a long position) raises the extreme price every
time the fill price sets a new high, and pulls the trigger price up to
`extreme - trailing_offset`. `side=BUY` (for closing a short position)
follows new lows, pulling the trigger price down to
`extreme + trailing_offset`. In both cases the trigger price never moves in
the adverse direction (monotonic) — even if the market reverses, the
protection level already secured is never lost. The isomorphic logic on
the backtest side (BT-6 `update_trailing_stop`) updates using bar high/low
and a ratio (trail_pct), but here the live tick and absolute-price offset
(trailing_offset) map directly onto the contract field
(`SubmitOrderCommand.trailing_offset`).
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
    """Returns the next state reflecting the new tick — the trigger price is
    compared against the previous value via `max`/`min` and never moves in
    the adverse direction (monotonic update)."""
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
