"""BT-15b — `vector/fills.py` <-> 이벤트 엔진(`quick_backtest`) 동등성 심화 (D2 보강).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(2/2) 깊이 하한 D2 보강 — 기존 파일(test_vector_event_parity.py)을 건드리지 않고
새 보조 파일로 D2 미달 항목(실패 주입 0, 게이트 적색 재현 0)을 채운다.

D2 Evidence:
- negative: test_zero_quantity_rejected, test_negative_quantity_rejected,
  test_empty_candles_rejected (3종)
- failure_injection: test_event_engine_failure_propagates_not_swallowed
- gate_red: test_parity_check_catches_corrupted_signal,
  test_perf_budget_goes_red_with_10x_injected_delay
- perf: test_vector_run_perf_budget (perf_budget.best_of, 8-bar series)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

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


def _config(**overrides: Any) -> BacktestConfigV2:
    base: dict[str, Any] = dict(
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
    base.update(overrides)
    return BacktestConfigV2(**base)


def _bool_signal(n: int, true_at: set[int]) -> BoolSignal:
    values = np.array([i in true_at for i in range(n)], dtype=np.bool_)
    return BoolSignal(values=values, na=np.zeros(n, dtype=np.bool_))


class _Scripted:
    """`quick_backtest` 테스트와 같은 스타일의 스크립트 전략."""

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


def _round_trip_signal(n: int) -> VectorSignal:
    """entries[1]=True, exits[4]=True — 진입/청산 왕복 신호."""
    return VectorSignal(_bool_signal(n, {1}), _bool_signal(n, {4}), _QTY)


_PLAN: dict[int, OrderIntent] = {
    1: OrderIntent(side=OrderSide.BUY, quantity=_QTY),
    4: OrderIntent(side=OrderSide.SELL, quantity=_QTY),
}


def _parity_holds(signal: VectorSignal, cols: CandleColumns) -> bool:
    """벡터 경로와 이벤트 경로의 체결·최종 equity 가 일치하는지 확인."""
    v, e = _vector(signal, cols), _event(cols, _PLAN)
    return v.fills == e.fills and v.final_equity == e.final_equity


# === negative tests (3종) ===


def test_zero_quantity_rejected() -> None:
    """quantity=0 은 VectorSignal 생성 단계에서 VectorFillsError 를 던진다."""
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(5, set()),
            exits=_bool_signal(5, set()),
            quantity=Decimal("0"),
        )


def test_negative_quantity_rejected() -> None:
    """quantity<0 도 동일하게 VectorFillsError 를 던진다."""
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(5, set()),
            exits=_bool_signal(5, set()),
            quantity=Decimal("-10"),
        )


def test_empty_candles_rejected() -> None:
    """빈 CandleColumns 로 run_vector_backtest 를 호출하면 QuickBacktestInputError."""
    from src.foundation.backtest.application.quick_backtest_fill import (
        QuickBacktestInputError,
    )

    empty_cols = CandleColumns(
        ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[]
    )
    signal = VectorSignal(
        entries=_bool_signal(0, set()),
        exits=_bool_signal(0, set()),
        quantity=Decimal("1"),
    )
    with pytest.raises(QuickBacktestInputError):
        run_vector_backtest(
            _config(),
            empty_cols,
            signal,
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )


# === failure injection ===


def test_event_engine_failure_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the delegated event engine fails, the vector path must raise,
    not return a result."""

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

    Budget rationale: local measurement is expected in the low single-digit ms;
    500 ms leaves a margin well over 30x (measured value recorded in the task note).
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
