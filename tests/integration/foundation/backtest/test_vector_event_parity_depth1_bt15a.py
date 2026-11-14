"""BT-15a D2 depth floor — vector <-> event parity: negative, failure injection, gate-red, perf.

Auxiliary file (the existing parity tests stay untouched). The name carries `_bt15a` because
`test_vector_event_parity_depth1.py` already exists (task-24821, BT-15b).

D2 evidence:
- negative: test_signal_length_mismatch_rejected, test_entries_exits_length_mismatch_rejected,
  test_nan_quantity_rejected, test_na_entry_signal_never_fills
- failure_injection: test_event_engine_failure_propagates_not_swallowed
- gate_red: test_parity_check_catches_corrupted_signal (the parity assertion goes red when
  the vector input is corrupted), test_perf_budget_goes_red_with_10x_injected_delay
- perf: test_vector_run_perf_budget (perf_budget.best_of, 8-bar series)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
    QuickBacktestResult,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector import fills as fills_module
from src.foundation.backtest.vector.fills import (
    VectorFillsError,
    VectorSignal,
    run_vector_backtest,
)
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CASH = Decimal("100000")
_QTY = Decimal("10")
_CLOSES = ["100", "101", "102", "103", "104", "105", "106", "107"]


def _columns(closes: list[str]) -> CandleColumns:
    c = [Decimal(x) for x in closes]
    o = [c[0], *c[:-1]]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))],
        open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)],
        close=c,
        volume=[Decimal("1000")] * len(c),
        quote_volume=[None] * len(c),
    )


def _config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=Decimal("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=Decimal("2"), taker_bps=Decimal("5"), min_fee=Decimal("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )


def _bool_signal(n: int, true_at: set[int], na_at: frozenset[int] = frozenset()) -> BoolSignal:
    return BoolSignal(
        values=np.array([i in true_at for i in range(n)], dtype=np.bool_),
        na=np.array([i in na_at for i in range(n)], dtype=np.bool_),
    )


class _Scripted:
    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


def _vector(signal: VectorSignal, cols: CandleColumns) -> QuickBacktestResult:
    return run_vector_backtest(_config(), cols, signal, timeframe=Timeframe.M1, initial_cash=_CASH)


def _event(cols: CandleColumns, plan: dict[int, OrderIntent]) -> QuickBacktestResult:
    return run_quick_backtest(
        _config(), cols, timeframe=Timeframe.M1, strategy=_Scripted(plan), initial_cash=_CASH
    )


_PLAN = {
    1: OrderIntent(side=OrderSide.BUY, quantity=_QTY),
    4: OrderIntent(side=OrderSide.SELL, quantity=_QTY),
}


def _parity_holds(signal: VectorSignal, cols: CandleColumns) -> bool:
    v, e = _vector(signal, cols), _event(cols, _PLAN)
    return v.fills == e.fills and v.final_equity == e.final_equity


def _round_trip_signal(n: int) -> VectorSignal:
    return VectorSignal(_bool_signal(n, {1}), _bool_signal(n, {4}), _QTY)


# === negative ===


def test_signal_length_mismatch_rejected() -> None:
    cols = _columns(_CLOSES)
    with pytest.raises(VectorFillsError):
        _vector(_round_trip_signal(len(cols) - 1), cols)


def test_entries_exits_length_mismatch_rejected() -> None:
    with pytest.raises(VectorFillsError):
        VectorSignal(_bool_signal(5, {1}), _bool_signal(6, {4}), _QTY)


def test_nan_quantity_rejected() -> None:
    with pytest.raises(VectorFillsError):
        VectorSignal(_bool_signal(5, {1}), _bool_signal(5, set()), Decimal("NaN"))


def test_na_entry_signal_never_fills() -> None:
    """An entry that is true but marked NA must not open a position."""
    cols = _columns(_CLOSES)
    signal = VectorSignal(
        _bool_signal(len(cols), {1}, na_at=frozenset({1})), _bool_signal(len(cols), set()), _QTY
    )
    assert len(_vector(signal, cols).fills) == 0


# === failure injection ===


def test_event_engine_failure_propagates_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the delegated event engine fails, the vector path must raise, not return a result."""

    def _boom(*_a: object, **_k: object) -> QuickBacktestResult:
        raise RuntimeError("injected event engine failure")

    monkeypatch.setattr(fills_module, "run_quick_backtest", _boom)
    cols = _columns(_CLOSES)
    with pytest.raises(RuntimeError, match="injected"):
        _vector(_round_trip_signal(len(cols)), cols)


# === gate-red reproduction ===


def test_parity_check_catches_corrupted_signal() -> None:
    """Baseline parity holds; a corrupted exit index makes the same assertion go red."""
    cols = _columns(_CLOSES)
    assert _parity_holds(_round_trip_signal(len(cols)), cols)
    corrupted = VectorSignal(_bool_signal(len(cols), {1}), _bool_signal(len(cols), {5}), _QTY)
    assert not _parity_holds(corrupted, cols)


# === performance ===


@pytest.mark.perf
def test_vector_run_perf_budget(perf_budget: PerfBudget) -> None:
    """8-bar vector run stays under 500 ms CPU (best of 5).

    Budget rationale: local measurement is expected in the low single-digit ms; 500 ms leaves
    a margin well over 30x (measured value recorded in the task note).
    """
    cols = _columns(_CLOSES)
    signal = _round_trip_signal(len(cols))
    sample = perf_budget.best_of(lambda: _vector(signal, cols), n=5)
    assert sample.cpu_ms < 500.0, perf_budget.describe(sample, budget_ms=500.0)


@pytest.mark.perf
def test_perf_budget_goes_red_with_10x_injected_delay(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Virtual 10x delay (5 s on a 500 ms budget) must push the measurement over budget."""
    reset_virtual_totals()
    delayed = virtual_delayed_sync(fills_module.run_quick_backtest, 5.0)
    monkeypatch.setattr(fills_module, "run_quick_backtest", delayed)
    cols = _columns(_CLOSES)
    signal = _round_trip_signal(len(cols))
    sample = perf_budget.best_of(lambda: _vector(signal, cols), n=3)
    assert sample.cpu_ms >= 500.0
    reset_virtual_totals()
