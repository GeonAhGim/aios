"""BT-6 — Order type trigger model (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-6, §3.4(`order_types: {limit, stop, oco, trailing}`).

Each function determines only "was this bar triggering?" — the calculation of fill
price after trigger is the responsibility of the slippage model (BT-2, `slippage.py`)
and must not be mixed here (the 5 files do not import each other). When a disabled
order type is passed via `OrderTypesConfig`, it is rejected with `OrderTypeDisabledError`
(as documented in the `models_v2.py` docstring delegating responsibility to this module).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models_v2 import OrderTypesConfig

OrderTypeName = Literal["limit", "stop", "oco", "trailing"]


class OrderTypeDisabledError(ValueError):
    """Order entered with a type disabled in `OrderTypesConfig`."""


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def ensure_order_type_enabled(config: OrderTypesConfig, order_type: OrderTypeName) -> None:
    enabled = {
        "limit": config.limit,
        "stop": config.stop,
        "oco": config.oco,
        "trailing": config.trailing,
    }[order_type]
    if not enabled:
        raise OrderTypeDisabledError(f"order_type={order_type}는 비활성화된 설정이다")


def is_limit_triggered(
    *, side: OrderSide, limit_price: Decimal, bar_low: Decimal, bar_high: Decimal
) -> bool:
    """For a buy limit order, it can fill on a bar if the low reaches the limit price
    or lower; for a sell limit order, it can fill if the high reaches the limit price
    or higher."""

    _reject_negative_or_nan(limit_price, "limit_price")
    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    if side == OrderSide.BUY:
        return bar_low <= limit_price
    return bar_high >= limit_price


def is_stop_triggered(
    *, side: OrderSide, stop_price: Decimal, bar_low: Decimal, bar_high: Decimal
) -> bool:
    """A buy stop (short cover or breakout buy) triggers if the high reaches the stop
    price or higher; a sell stop (stop-loss) triggers if the low reaches the stop price
    or lower."""

    _reject_negative_or_nan(stop_price, "stop_price")
    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    if side == OrderSide.BUY:
        return bar_high >= stop_price
    return bar_low <= stop_price


@dataclass(frozen=True, slots=True)
class OcoResolution:
    triggered_leg: Literal["a", "b", "none"]
    cancelled_leg: Literal["a", "b", "none"]


def resolve_oco(
    *, leg_a_triggered: bool, leg_b_triggered: bool, priority_leg: Literal["a", "b"]
) -> OcoResolution:
    """When one leg fills, the other is immediately cancelled.

    When both legs are touched within the same bar (a wide bar whose high-low range
    covers both leg trigger prices), a real exchange provides no way to know which leg
    actually filled first from the bar data alone. Instead of silently hiding this
    ambiguity, the caller is forced to explicitly specify a conservative assumption via
    `priority_leg` (e.g., loss leg priority).
    """

    if leg_a_triggered and leg_b_triggered:
        if priority_leg == "a":
            return OcoResolution(triggered_leg="a", cancelled_leg="b")
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    if leg_a_triggered:
        return OcoResolution(triggered_leg="a", cancelled_leg="b")
    if leg_b_triggered:
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    return OcoResolution(triggered_leg="none", cancelled_leg="none")


@dataclass(frozen=True, slots=True)
class TrailingStopState:
    extreme_price: Decimal
    stop_price: Decimal


def update_trailing_stop(
    *,
    side: OrderSide,
    state: TrailingStopState,
    bar_low: Decimal,
    bar_high: Decimal,
    trail_pct: Decimal,
) -> TrailingStopState:
    """`side=SELL` (trailing stop to close a long position) raises the extreme price
    whenever a new high appears and raises the stop to `extreme_price * (1 - trail_pct)`.
    `side=BUY` (trailing stop to close a short position) follows new lows and lowers the
    stop to `extreme_price * (1 + trail_pct)` — the extreme price never moves in the
    unfavorable direction (monotonic).
    """

    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    _reject_negative_or_nan(trail_pct, "trail_pct")
    _reject_negative_or_nan(state.extreme_price, "state.extreme_price")

    if side == OrderSide.SELL:
        new_extreme = max(state.extreme_price, bar_high)
        new_stop = new_extreme * (Decimal(1) - trail_pct)
    else:
        new_extreme = min(state.extreme_price, bar_low)
        new_stop = new_extreme * (Decimal(1) + trail_pct)
    return TrailingStopState(extreme_price=new_extreme, stop_price=new_stop)


@dataclass(frozen=True, slots=True)
class OcaResolution:
    """N-way generalization of `OcoResolution` -- task-2623 bracket exit
    (profit/loss/trail legs). Isomorphic to the live path's
    `src/services/oms/domain/order_types/oca.py::resolve_oca` (same
    signature, same decision rule) -- parity between the two is asserted by
    `tests/unit/oms/test_bracket_oca_parity.py`, not by importing one from
    the other (foundation/backtest and services/oms are separate bounded
    contexts, deliberately duplicated the same way `resolve_oco` already
    is on both sides)."""

    triggered_leg: str | None
    cancelled_legs: tuple[str, ...]


def resolve_oca(*, triggered: Mapping[str, bool], priority_order: Sequence[str]) -> OcaResolution:
    """Bracket exit is an OCA (one-cancels-all) group of up to 3 legs
    (`profit`/`loss`/`trail`). The first leg in `priority_order` that is
    triggered wins; every other leg in the group is cancelled -- including
    legs that also triggered on the same bar (the ambiguous-tie case
    `resolve_oco` already documents: caller states a conservative
    assumption via ordering instead of this module guessing). No leg
    triggered -> `triggered_leg=None`, nothing cancelled yet."""

    if not priority_order:
        raise ValueError("priority_order는 최소 1개 레그가 필요하다")
    if set(triggered) != set(priority_order):
        raise ValueError("triggered와 priority_order의 레그 집합이 일치해야 한다")
    for leg in priority_order:
        if triggered[leg]:
            cancelled = tuple(other for other in priority_order if other != leg)
            return OcaResolution(triggered_leg=leg, cancelled_legs=cancelled)
    return OcaResolution(triggered_leg=None, cancelled_legs=())


def bracket_quantity_for_fill(*, requested_qty: Decimal, filled_qty: Decimal) -> Decimal:
    """Bracket exit legs must never cover more than what the entry actually
    filled (partial-fill quantity parity) -- if the entry only partially
    fills, the bracket's exit legs are sized to `filled_qty`, never the
    originally requested quantity. Isomorphic to the live-side function of
    the same name in `src/services/oms/domain/order_types/oca.py`."""

    _reject_negative_or_nan(requested_qty, "requested_qty")
    _reject_negative_or_nan(filled_qty, "filled_qty")
    return min(requested_qty, filled_qty)
