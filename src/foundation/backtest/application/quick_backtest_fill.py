"""BT-10 — Lifecycle of a single order in instant backtest (compose BT-2..8, no re-implementation).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-10,
§3.4. Split from `quick_backtest.py` (loop/strategy touchpoint) to keep this file
under 300 lines — its sole responsibility is "how one pending order fills at what
price and quantity on which bar, and how position cost settles"; it does not iterate
bars or call strategies.

Delegation table (domain leaf each function calls):
- `submit_order`   → BT-4 `latency.resolve_execution_bar_index`,
                     BT-6 `order_types.ensure_order_type_enabled`
- `price_path`     → BT-7 `magnifier.magnify` (sub-TF slices are cut here)
- `try_fill`       → BT-6 `is_limit_triggered`/`is_stop_triggered`,
                     BT-5 `compute_partial_fill` (carryover is caller's job),
                     BT-2 `apply_slippage`, BT-3 `compute_commission`
- `settle_costs`   → BT-8 `compute_funding_cost`/`compute_borrow_cost`

Pure functions only — no I/O, no clock, no randomness; amounts are all `Decimal`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.costs.borrow import compute_borrow_cost
from src.foundation.backtest.domain.costs.funding import compute_funding_cost
from src.foundation.backtest.domain.fill.commission import compute_commission
from src.foundation.backtest.domain.fill.latency import resolve_execution_bar_index
from src.foundation.backtest.domain.fill.order_types import (
    ensure_order_type_enabled,
    is_limit_triggered,
    is_stop_triggered,
)
from src.foundation.backtest.domain.fill.partial_fill import compute_partial_fill
from src.foundation.backtest.domain.fill.slippage import apply_slippage
from src.foundation.backtest.domain.magnifier import HigherBar, magnify
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration

OrderType = Literal["market", "limit", "stop"]
_ZERO = Decimal("0")


class QuickBacktestInputError(ValueError):
    """`BT_QUICK_INPUT` — the input itself violates the contract (fail-closed)."""


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Order intent the strategy places on a single bar. `trigger_price` is required
    for `limit`/`stop`. OCO and trailing orders are out of scope for instant backtest
    (BT-11) and cannot be expressed here."""

    side: OrderSide
    quantity: Decimal
    order_type: OrderType = "market"
    trigger_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FillEvent:
    bar_index: int
    open_time: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal
    commission: Decimal
    remaining_quantity: Decimal  # Carryover from partial fill (BT-5) — 0 = fully filled


@dataclass(slots=True)
class PendingOrder:
    intent: OrderIntent
    remaining: Decimal
    execution_index: int  # First fillable bar as determined by BT-4


@dataclass(frozen=True, slots=True)
class Holding:
    """Unit for cost settlement (BT-8) — from when position leaves 0 until it returns."""

    opened_at: datetime
    side: OrderSide
    notional: Decimal


def submit_order(
    config: BacktestConfigV2, intent: OrderIntent, columns: CandleColumns, signal_index: int,
    *, timeframe: Timeframe,
) -> PendingOrder | None:
    """Determines the first fillable bar for an order submitted at the open_time of
    bar `signal_index`, per BT-4. Returns `None` if no such bar exists within the
    data range (gap or end-of-data = expired)."""
    if intent.quantity.is_nan() or intent.quantity <= 0:
        raise QuickBacktestInputError(f"주문 수량은 양수여야 한다: {intent.quantity}")
    if intent.order_type != "market":
        ensure_order_type_enabled(config.order_types, intent.order_type)
        if intent.trigger_price is None:
            raise QuickBacktestInputError(f"{intent.order_type} 주문은 trigger_price가 필요하다")
    step_ms = int(duration(timeframe).total_seconds() * 1000)
    tail_start = signal_index + 1
    span = config.latency_ms // step_ms + 2  # latency span + margin — max bars to copy
    short_tail = columns.ts[tail_start : tail_start + span]
    offset = _resolve_offset(config, columns, signal_index, short_tail)
    if offset is None and tail_start + span < len(columns):
        # Copy the full tail only when not within a short window (gap) — avoids O(n) copy per order.
        offset = _resolve_offset(config, columns, signal_index, columns.ts[tail_start:])
    if offset is None:
        return None
    return PendingOrder(
        intent=intent, remaining=intent.quantity, execution_index=tail_start + offset
    )


def _resolve_offset(
    config: BacktestConfigV2, columns: CandleColumns, signal_index: int, tail: list[datetime]
) -> int | None:
    try:
        return resolve_execution_bar_index(
            submitted_at=columns.ts[signal_index], latency_ms=config.latency_ms, bar_open_times=tail
        )
    except LookupError:
        return None


def _slice_lower(
    lower: CandleColumns, cursor: int, start: datetime, end: datetime
) -> tuple[CandleColumns, int]:
    while cursor < len(lower) and lower.ts[cursor] < start:
        cursor += 1
    stop = cursor
    while stop < len(lower) and lower.ts[stop] < end:
        stop += 1
    sliced = CandleColumns(
        ts=lower.ts[cursor:stop], open=lower.open[cursor:stop], high=lower.high[cursor:stop],
        low=lower.low[cursor:stop], close=lower.close[cursor:stop],
        volume=lower.volume[cursor:stop], quote_volume=lower.quote_volume[cursor:stop],
    )
    return sliced, stop


def price_path(
    config: BacktestConfigV2, columns: CandleColumns, i: int, *, timeframe: Timeframe,
    lower_columns: CandleColumns | None, lower_cursor: int,
) -> tuple[tuple[Decimal, ...], int]:
    """Price visit order on bar `i` (BT-7) and the advanced sub-bar cursor. Sub-bars
    are assumed sorted chronologically (adapter ORDER BY); the cursor only moves
    forward, yielding O(n) total."""
    bar = HigherBar(
        open_time=columns.ts[i], open=columns.open[i], high=columns.high[i],
        low=columns.low[i], close=columns.close[i],
    )
    lower_slice = None
    if config.magnifier_tf is not None and lower_columns is not None:
        lower_slice, lower_cursor = _slice_lower(
            lower_columns, lower_cursor, bar.open_time, bar.open_time + duration(timeframe)
        )
    path = magnify(
        bar, higher_tf=timeframe, magnifier_tf=config.magnifier_tf, lower_bars=lower_slice
    )
    return path, lower_cursor


def _triggered(intent: OrderIntent, trigger: Decimal, lo: Decimal, hi: Decimal) -> bool:
    if intent.order_type == "limit":
        return is_limit_triggered(side=intent.side, limit_price=trigger, bar_low=lo, bar_high=hi)
    return is_stop_triggered(side=intent.side, stop_price=trigger, bar_low=lo, bar_high=hi)


def _reference_price(intent: OrderIntent, path: tuple[Decimal, ...]) -> Decimal | None:
    """Sweep the price visit order by segment to find the reference price at the
    first point where the BT-6 trigger is touched. If the segment's open price
    already crossed the trigger (gap), fill at that open price."""
    if intent.order_type == "market":
        return path[0]
    trigger = intent.trigger_price
    if trigger is None:  # path already rejected by submit_order — defensive fail-closed
        raise QuickBacktestInputError(f"{intent.order_type} 주문은 trigger_price가 필요하다")
    prev = path[0]
    for point in path:
        if _triggered(intent, trigger, min(prev, point), max(prev, point)):
            return prev if _triggered(intent, trigger, prev, prev) else trigger
        prev = point
    return None


def try_fill(
    config: BacktestConfigV2, pending: PendingOrder, i: int, columns: CandleColumns,
    path: tuple[Decimal, ...],
) -> FillEvent | None:
    """Attempt to fill the pending order on bar `i`. Returns `None` if the trigger
    is not hit or volume is zero (caller carries it to the next bar)."""
    intent = pending.intent
    reference = _reference_price(intent, path)
    if reference is None:
        return None
    volume = columns.volume[i]
    outcome = compute_partial_fill(
        config.partial_fill, order_quantity=pending.remaining, bar_volume=volume
    )
    if outcome.filled_quantity == 0:
        return None
    price = apply_slippage(
        config.slippage, side=intent.side, reference_price=reference,
        quantity=outcome.filled_quantity, bar_volume=volume,
    )
    commission = compute_commission(
        config.commission, is_maker=intent.order_type == "limit",
        notional=price * outcome.filled_quantity,
    )
    return FillEvent(
        bar_index=i, open_time=columns.ts[i], side=intent.side, order_type=intent.order_type,
        quantity=outcome.filled_quantity, price=price, commission=commission,
        remaining_quantity=outcome.remaining_quantity,
    )


def settle_costs(
    config: BacktestConfigV2, holding: Holding, exit_time: datetime, funding_rate: Decimal | None
) -> tuple[Decimal, Decimal]:
    """(funding, borrow) costs. When `funding=False`, BT-8 ignores the rate and
    returns 0; when `True`, the caller (`run_quick_backtest`) pre-enforced that
    a rate exists."""
    rate = funding_rate if funding_rate is not None else _ZERO
    funding = compute_funding_cost(
        config.costs, side=holding.side, notional=holding.notional, funding_rate=rate,
        entry_time=holding.opened_at, exit_time=exit_time,
    )
    borrow = _ZERO
    if holding.side == OrderSide.SELL:  # borrow cost applies to short (sell) positions only
        borrow = compute_borrow_cost(
            config.costs, notional=holding.notional, entry_time=holding.opened_at,
            exit_time=exit_time,
        )
    return funding, borrow
