"""BT-10 bracket exit leg resolution, split out of quick_backtest.py (P6.line_cap 300줄).

`run_quick_backtest`'s per-bar loop delegates bracket exit (profit/loss/trail)
trigger checking and fill generation here so the main loop stays readable —
same fill-model contract as `quick_backtest_fill.py` (BT-2~8), no reimplementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest_fill import FillEvent
from src.foundation.backtest.domain.fill import order_types as bt6
from src.foundation.market_data.api import CandleColumns

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class BracketMetadata:
    """Bracket exit legs configured by strategy.bracket() on bar 0.

    Each leg (profit/loss/trail) is optional (None = leg not active).
    At least one leg must be present (validated at intent creation).
    All prices are limit/stop prices for the respective exit leg.
    trail_pct is a trailing stop percentage (e.g., Decimal("0.05") = 5% trail).
    """
    requested_qty: Decimal  # Requested exit quantity (before partial-fill adjustment)
    profit_price: Decimal | None  # Take-profit limit price (sell at >= this for BUY position)
    loss_price: Decimal | None  # Stop-loss limit price (sell at <= this for BUY position)
    trail_pct: Decimal | None  # Trailing stop percentage


@dataclass(slots=True)
class BracketExitState:
    """Mutable state for tracking bracket exit resolution (used internally in quick_backtest)."""
    requested_qty: Decimal
    filled_qty: Decimal
    profit_price: Decimal | None
    loss_price: Decimal | None
    trail_pct: Decimal | None
    resolved: bool = False


def resolve_bracket_exit(
    columns: CandleColumns, bar_index: int, qty: Decimal, state: BracketExitState
) -> FillEvent | None:
    """Check if any bracket exit leg triggers on this bar and generate a fill if so.

    Uses bt6.is_limit_triggered/is_stop_triggered to determine leg triggers,
    bt6.resolve_oca to handle simultaneous triggers, and bt6.bracket_quantity_for_fill
    to size the exit correctly per entry fill.
    """
    bar_low = columns.low[bar_index]
    bar_high = columns.high[bar_index]

    # Determine which legs are present and build the trigger map
    legs_present: dict[str, bool] = {}
    legs_in_priority_order = []  # Will order them for resolve_oca

    if state.loss_price is not None:
        legs_present["loss"] = bt6.is_stop_triggered(
            side=OrderSide.SELL if qty > 0 else OrderSide.BUY,
            stop_price=state.loss_price,
            bar_low=bar_low,
            bar_high=bar_high,
        )
        legs_in_priority_order.append("loss")

    if state.profit_price is not None:
        legs_present["profit"] = bt6.is_limit_triggered(
            side=OrderSide.SELL if qty > 0 else OrderSide.BUY,
            limit_price=state.profit_price,
            bar_low=bar_low,
            bar_high=bar_high,
        )
        legs_in_priority_order.append("profit")

    if state.trail_pct is not None:
        # Trailing stops not yet fully implemented in BT-6/10 — reject for now
        legs_present["trail"] = False
        legs_in_priority_order.append("trail")

    # If no legs present, nothing to check
    if not legs_in_priority_order:
        return None

    # Use resolve_oca to determine which leg wins (if any)
    resolution = bt6.resolve_oca(triggered=legs_present, priority_order=legs_in_priority_order)
    if resolution.triggered_leg is None:
        return None

    # Compute exit quantity using bracket_quantity_for_fill
    exit_qty = bt6.bracket_quantity_for_fill(
        requested_qty=state.requested_qty,
        filled_qty=state.filled_qty,
    )
    if exit_qty == 0:
        return None

    # Determine fill price based on which leg triggered.
    # Invariant: if triggered_leg is set, the corresponding price must be non-None
    # (we only add legs to legs_present if their prices are not None).
    if resolution.triggered_leg == "loss":
        if state.loss_price is None:
            raise RuntimeError("resolve_oca triggered 'loss' leg with no loss_price set")
        fill_price = state.loss_price
    elif resolution.triggered_leg == "profit":
        if state.profit_price is None:
            raise RuntimeError("resolve_oca triggered 'profit' leg with no profit_price set")
        fill_price = state.profit_price
    else:
        # Trailing stop not yet implemented
        return None

    # Generate FillEvent for the bracket exit.
    # Exit side is opposite of entry: if we're long (qty > 0), we sell (SELL)
    exit_side = OrderSide.SELL if qty > 0 else OrderSide.BUY

    return FillEvent(
        bar_index=bar_index,
        open_time=columns.ts[bar_index],
        side=exit_side,
        order_type="market",
        quantity=exit_qty,
        price=fill_price,
        commission=_ZERO,  # Bracket exit commission not yet modeled
        remaining_quantity=_ZERO,
    )
