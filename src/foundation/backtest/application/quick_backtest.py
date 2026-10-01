"""BT-10 — Quick backtest (columnar path + BT-2~8 fill-model assembly).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 (`application/quick_backtest.py` chart-range quick backtest, columnar
path, bar-count cap), §3.4, §7 (1-month M1 ≤5s), §9.5 BT-10,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md #1·#3.

Does not reimplement any part of the fill model — a single order's lifecycle
(delegated to BT-2~8) lives in `quick_backtest_fill.py`; this file only does
bar iteration, the strategy touchpoint, position/cash updates, and result
assembly.

Candle input stays as the LA-23b columnar `CandleColumns` (ADR-A #1) — it is
iterated by index, with no per-record pydantic reconstruction. This module
does no I/O (TID251: asyncpg is forbidden in backtest/application). The
caller (BT-13 router / BT-11 job) reads once with
`columns = await store.read_candles_columnar(conn, key, start, end, as_of)`
(one round trip) and hands it over — the integration test proves that path
directly.

v1 (`run_backtest.py`, `BacktestConfig`) is left untouched; it coexists with
the v2 (`BacktestConfigV2`, BT-1) path (no. 107 §3.3).

Determinism: no global clock, randomness, or dict/set iteration — all
amounts are `Decimal` — the same (config, columns, strategy) input always
produces a byte-identical fill log.

No look-ahead (fail-closed): the strategy receives `BarWindow`, a read-only
view visible only up to the current bar — any index access beyond that
raises `LookAheadError`. A signal is treated as submitted at bar j's
open_time, and since BT-4 (strict-exceed) picks the fill bar, the fill bar
is always after j (same principle as `domain/rules.is_look_ahead_safe`).

Out of scope (BT-11/12): OCO/trailing, checkpoint/progress, performance
metrics (tearsheet). The execution-time cap needs clock access that would
break determinism, so it is the caller's responsibility (async timeout);
here we only enforce the bar-count cap (`MAX_QUICK_BARS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest_bracket import (
    BracketExitState,
    BracketMetadata,
    resolve_bracket_exit,
)
from src.foundation.backtest.application.quick_backtest_fill import (
    FillEvent,
    Holding,
    OrderIntent,
    PendingOrder,
    QuickBacktestInputError,
    price_path,
    settle_costs,
    submit_order,
    try_fill,
)
from src.foundation.backtest.domain.magnifier import validate_magnifier_config
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.api import CandleColumns, duration
from src.foundation.market_data.contracts.v1 import Timeframe

__all__ = [
    "MAX_QUICK_BARS",
    "BarWindow",
    "BracketMetadata",
    "FillEvent",
    "LookAheadError",
    "OrderIntent",
    "PositionState",
    "QuickBacktestInputError",
    "QuickBacktestResult",
    "SignalSource",
    "TooManyBarsError",
    "run_quick_backtest",
]

MAX_QUICK_BARS = (
    44_640  # 31 days x 1,440 -- 1-month M1 cap (§2.5 "bar-count cap", §7 1-month basis)
)
_ZERO = Decimal("0")


class TooManyBarsError(QuickBacktestInputError):
    """`BT_QUICK_TOO_MANY_BARS` -- quick-backtest bar-count cap exceeded
    (target for BT-11 deep backtest)."""

    details: dict[str, int]


class LookAheadError(IndexError):
    """`BT_QUICK_LOOK_AHEAD` -- strategy tried to read a bar beyond the current one."""


@dataclass(frozen=True, slots=True)
class PositionState:
    quantity: Decimal
    cash: Decimal
    has_pending_order: bool


class BarWindow:
    """Read-only view exposing only `columns[0:end)` (no copy). Negative
    indices are relative to the window end."""

    __slots__ = ("_columns", "_end")

    def __init__(self, columns: CandleColumns, end_exclusive: int) -> None:
        self._columns = columns
        self._end = end_exclusive

    def __len__(self) -> int:
        return self._end

    def _index(self, i: int) -> int:
        j = i + self._end if i < 0 else i
        if not 0 <= j < self._end:
            raise LookAheadError(f"index {i}는 현재 창(길이 {self._end}) 밖이다 — 미래 참조 금지")
        return j

    def ts(self, i: int) -> datetime:
        return self._columns.ts[self._index(i)]

    def open(self, i: int) -> Decimal:
        return self._columns.open[self._index(i)]

    def high(self, i: int) -> Decimal:
        return self._columns.high[self._index(i)]

    def low(self, i: int) -> Decimal:
        return self._columns.low[self._index(i)]

    def close(self, i: int) -> Decimal:
        return self._columns.close[self._index(i)]

    def volume(self, i: int) -> Decimal:
        return self._columns.volume[self._index(i)]


class SignalSource(Protocol):
    """Strategy touchpoint. The DSL-11 facade wraps its compiled output in this
    shape (I-05) -- this module knows nothing about the strategy internals."""

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None: ...


@dataclass(frozen=True, slots=True)
class QuickBacktestResult:
    fills: tuple[FillEvent, ...]
    equity_curve: tuple[Decimal, ...]
    final_equity: Decimal
    cash: Decimal
    position_quantity: Decimal
    funding_cost: Decimal
    borrow_cost: Decimal
    bars: int
    expired_orders: int  # Orders dropped because no fillable bar existed within the data range
    warnings: tuple[str, ...]


def _validate(
    config: BacktestConfigV2,
    columns: CandleColumns,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None,
    max_bars: int,
) -> None:
    n = len(columns)
    if n == 0:
        raise QuickBacktestInputError("캔들이 0개다 — 백테스트할 구간이 없다")
    if n > max_bars:
        raise TooManyBarsError(f"봉 수 {n}이 즉시 백테스트 상한 {max_bars}을 넘는다 — BT-11 대상")
    arrays = (columns.open, columns.high, columns.low, columns.close, columns.volume)
    if any(len(a) != n for a in arrays):
        raise QuickBacktestInputError("CandleColumns 배열 길이가 서로 다르다")
    if initial_cash.is_nan() or initial_cash < 0:
        raise QuickBacktestInputError(f"initial_cash는 음수·NaN을 허용하지 않는다: {initial_cash}")
    if config.costs.funding and funding_rate is None:
        raise QuickBacktestInputError(
            "costs.funding=True면 funding_rate가 필요하다 — 0으로 조용히 채우지 않는다"
        )
    validate_magnifier_config(higher_tf=timeframe, magnifier_tf=config.magnifier_tf)


def run_quick_backtest(
    config: BacktestConfigV2,
    columns: CandleColumns,
    *,
    timeframe: Timeframe,
    strategy: SignalSource,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
    max_bars: int = MAX_QUICK_BARS,
    bracket: BracketMetadata | None = None,
) -> QuickBacktestResult:
    """Evaluates `strategy` once per bar over `columns` (LA-23b columnar path,
    `timeframe` bars) and computes fills/costs via BT-2~8. At most one pending
    order at a time -- a new intent replaces (cancels) the existing pending
    order. When a bracket is configured, after the entry fills this watches
    the bracket exit legs (profit/loss/trail) on subsequent bars and handles
    the exit via resolve_oca/bracket_quantity_for_fill."""
    _validate(config, columns, timeframe, initial_cash, funding_rate, max_bars)

    # Extract bracket metadata from strategy if present (duck typing for _MaterializedSignalSource).
    if bracket is None:
        bracket = getattr(strategy, "bracket", None)
    n = len(columns)
    step = duration(timeframe)
    warnings: list[str] = []
    if config.magnifier_tf is not None and lower_columns is None:
        warnings.append(
            "magnifier_tf가 설정됐지만 lower_columns가 없어 봉 단위 확대로 대체했다(BT-7 (4))"
        )

    cash, qty = initial_cash, _ZERO
    pending: PendingOrder | None = None
    holding: Holding | None = None
    bracket_exit: BracketExitState | None = None  # Tracks active bracket exit resolution
    funding_total = borrow_total = _ZERO
    fills: list[FillEvent] = []
    equity: list[Decimal] = []
    expired = 0
    lower_cursor = 0

    for i in range(n):
        if pending is not None and i >= pending.execution_index:
            path, lower_cursor = price_path(
                config,
                columns,
                i,
                timeframe=timeframe,
                lower_columns=lower_columns,
                lower_cursor=lower_cursor,
            )
            fill = try_fill(config, pending, i, columns, path)
            if fill is not None:
                fills.append(fill)
                signed = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
                cash -= fill.price * signed + fill.commission
                before, qty = qty, qty + signed
                if holding is not None and (qty == 0 or (before > 0) != (qty > 0)):
                    f_cost, b_cost = settle_costs(config, holding, fill.open_time, funding_rate)
                    funding_total, borrow_total = funding_total + f_cost, borrow_total + b_cost
                    cash -= f_cost + b_cost
                    holding = None
                if holding is None and qty != 0:
                    holding = Holding(
                        opened_at=fill.open_time,
                        side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                        notional=abs(qty) * fill.price,
                    )
                pending.remaining = fill.remaining_quantity
                if pending.remaining == 0:
                    pending = None

                # If bracket configured and entry filled, initialize bracket exit tracking.
                if bracket is not None and bracket_exit is None and qty != 0:
                    bracket_exit = BracketExitState(
                        requested_qty=bracket.requested_qty,
                        filled_qty=fill.quantity,
                        profit_price=bracket.profit_price,
                        loss_price=bracket.loss_price,
                        trail_pct=bracket.trail_pct,
                    )

        # Check bracket exit legs and generate exit fills when triggered.
        if bracket_exit is not None and not bracket_exit.resolved and qty != 0:
            bracket_exit_fill = resolve_bracket_exit(columns, i, qty, bracket_exit)
            if bracket_exit_fill is not None:
                fills.append(bracket_exit_fill)
                exit_signed = (
                    bracket_exit_fill.quantity
                    if bracket_exit_fill.side == OrderSide.BUY
                    else -bracket_exit_fill.quantity
                )
                cash -= bracket_exit_fill.price * exit_signed + bracket_exit_fill.commission
                before, qty = qty, qty + exit_signed
                if holding is not None and (qty == 0 or (before > 0) != (qty > 0)):
                    f_cost, b_cost = settle_costs(
                        config, holding, bracket_exit_fill.open_time, funding_rate
                    )
                    funding_total, borrow_total = funding_total + f_cost, borrow_total + b_cost
                    cash -= f_cost + b_cost
                    holding = None
                bracket_exit.resolved = True

        equity.append(cash + qty * columns.close[i])

        position = PositionState(quantity=qty, cash=cash, has_pending_order=pending is not None)
        intent = strategy.on_bar(BarWindow(columns, i + 1), position)
        if intent is not None:
            submitted = submit_order(config, intent, columns, i, timeframe=timeframe)
            if submitted is None:
                expired += 1
            else:
                pending = submitted

    if holding is not None:
        f_cost, b_cost = settle_costs(config, holding, columns.ts[n - 1] + step, funding_rate)
        funding_total, borrow_total = funding_total + f_cost, borrow_total + b_cost
        cash -= f_cost + b_cost
    if pending is not None:
        expired += 1
    return QuickBacktestResult(
        fills=tuple(fills),
        equity_curve=tuple(equity),
        final_equity=cash + qty * columns.close[n - 1],
        cash=cash,
        position_quantity=qty,
        funding_cost=funding_total,
        borrow_cost=borrow_total,
        bars=n,
        expired_orders=expired,
        warnings=tuple(warnings),
    )
