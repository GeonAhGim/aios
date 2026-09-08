"""BT-15b (2/2) — connects vector-signal arrays to the BT-2~6 event fill engine.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(2/2). Prerequisites: BT-15a `vector/{arrays,signals}.py`(13e16cc2), BT-2~6
fill realism(fa3afe4), `quick_backtest`(9a1ae87).

Fills are a state machine where pending-order remainder, position, and
funding/borrow settlement carry over across bars
(`quick_backtest_fill.PendingOrder`/`Holding`) — they can't be parallelized
with per-element-independent numpy array operations. So this module does not
rewrite the fill arithmetic as arrays (avoiding §C duplicated-context /
I-05 violation): it wraps the entry/exit `BoolSignal` that BT-15a `signals.py`
computes in one vectorized pass over the whole bar series ("vector" ends
here) in a `SignalSource` protocol adapter required by BT-10
`quick_backtest.run_quick_backtest`, and hands it straight to the existing
event loop that runs BT-2~6 unchanged. It follows the design the `arrays.py`
module docstring already declares (signal = float64 vector, fill = Decimal
event engine) — the event path is always what produces a trustworthy fill
log. Thanks to this design, fills and equity from the vector path and the
event path are always exactly identical, not merely approximate (since it's
the same function call).

Long-only (short signals are out of scope for this leaf — if needed, handle
them explicitly in a separate leaf; don't build it now on speculation).

Pure module — no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
    QuickBacktestResult,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["VectorFillsError", "VectorSignal", "run_vector_backtest"]


class VectorFillsError(ValueError):
    """`BT_VECTOR_FILLS` — fail-closed rejection for signal length mismatch or quantity
    contract violation."""


@dataclass(frozen=True, slots=True)
class VectorSignal:
    """Entry/exit boolean arrays that BT-15a computes in one pass over the
    whole bar series. If `entries[i]` is definitely true (not na) and the
    position is 0, market-buy at bar `i`; if `exits[i]` is definitely true
    and there is a position, market-sell the full position."""

    entries: BoolSignal
    exits: BoolSignal
    quantity: Decimal

    def __post_init__(self) -> None:
        if len(self.entries) != len(self.exits):
            raise VectorFillsError(
                f"entries 길이 {len(self.entries)} != exits 길이 {len(self.exits)}"
            )
        if self.quantity.is_nan() or self.quantity <= 0:
            raise VectorFillsError(f"quantity는 양수여야 한다: {self.quantity}")


def _is_true(signal: BoolSignal, i: int) -> bool:
    return not signal.na[i] and bool(signal.values[i])


class _VectorSignalSource:
    """`SignalSource` (BT-10 protocol) adapter that only looks up `VectorSignal`
    arrays. It only reads the last index of `window` (`len(window) - 1` = the
    current bar) — anything beyond that (t+1 onward) is already blocked by
    `BarWindow` raising `LookAheadError`, so this adapter can't access it at
    all."""

    __slots__ = ("_signal",)

    def __init__(self, signal: VectorSignal) -> None:
        self._signal = signal

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        i = len(window) - 1
        signal = self._signal
        if position.quantity == 0 and _is_true(signal.entries, i):
            return OrderIntent(side=OrderSide.BUY, quantity=signal.quantity)
        if position.quantity > 0 and _is_true(signal.exits, i):
            return OrderIntent(side=OrderSide.SELL, quantity=position.quantity)
        return None


def run_vector_backtest(
    config: BacktestConfigV2,
    columns: CandleColumns,
    signal: VectorSignal,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
) -> QuickBacktestResult:
    """Replays `signal` (the entry/exit arrays produced by BT-15a's vector
    path) over `columns`. All fills come from the BT-10 event engine
    (delegating to BT-2~6) — this function only differs in how it looks up
    signals (a precomputed array instead of a DSL strategy callback)."""
    if len(signal.entries) != len(columns):
        raise VectorFillsError(
            f"signal 길이 {len(signal.entries)} != 캔들 수 {len(columns)}"
        )
    return run_quick_backtest(
        config, columns, timeframe=timeframe, strategy=_VectorSignalSource(signal),
        initial_cash=initial_cash, funding_rate=funding_rate, lower_columns=lower_columns,
    )
