"""BT-10 quick_backtest D2 보강 — negative 3종·실패 주입·게이트 적색 재현·perf 예산.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-10, §9.5.
기존 테스트(test_quick_backtest.py 등)는 수정하지 않고 부족한 D2 항목만 보강한다.
DB 없이 순수 엔진 경로만 사용한다.

성능 예산(신설): 200봉 M1·시장가 주문 20건 `run_quick_backtest` 1회.
로컬 실측(best_of n=7, cpu) 약 6ms(0.05s/8회) — 예산 500ms는 실측의 30배 이상 여유다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import quick_backtest_fill as qf
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    LookAheadError,
    OrderIntent,
    PositionState,
    QuickBacktestInputError,
    QuickBacktestResult,
    TooManyBarsError,
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
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CASH = Decimal("100000")
_D = Decimal
_BUDGET_MS = 500.0  # 실측 ~6ms의 30배 이상 (모듈 docstring)
_BARS = 200


def _cols(n: int) -> CandleColumns:
    c = [_D(100 + (i % 7)) for i in range(n)]
    o = [c[0], *c[:-1]] if c else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(n)],
        open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)],
        close=c,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _cfg(**ov: Any) -> BacktestConfigV2:
    base: dict[str, Any] = {
        "slippage": FixedSlippage(bps=_D("5")),
        "commission": VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("6"), min_fee=_D("0")
        ),
        "latency_ms": 0,
        "partial_fill": PartialFillConfig(max_participation_pct=_D("1")),
        "order_types": OrderTypesConfig(limit=True, stop=True, oco=False, trailing=False),
        "magnifier_tf": None,
        "costs": CostsConfig(funding=False, borrow_apr=None),
        "adjustments": AdjustmentsConfig(splits=False, dividends=False),
        "calendar": "24x7",
    }
    base.update(ov)
    return BacktestConfigV2(**base)


class _Plan:
    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


def _run(cols: CandleColumns, plan: dict[int, OrderIntent], **kw: Any) -> QuickBacktestResult:
    cfg = kw.pop("cfg", None) or _cfg()
    return run_quick_backtest(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Plan(plan), initial_cash=_CASH, **kw
    )


def _orders(n_orders: int) -> dict[int, OrderIntent]:
    return {
        i * 5: OrderIntent(side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL, quantity=_D("1"))
        for i in range(n_orders)
    }


# --- negative ----------------------------------------------------------------


def test_empty_candles_rejected() -> None:
    with pytest.raises(QuickBacktestInputError):
        _run(_cols(0), {})


def test_max_bars_exceeded_rejected() -> None:
    with pytest.raises(TooManyBarsError):
        _run(_cols(10), {}, max_bars=5)


def test_negative_initial_cash_rejected() -> None:
    with pytest.raises(QuickBacktestInputError):
        run_quick_backtest(
            _cfg(), _cols(5), timeframe=Timeframe.M1, strategy=_Plan({}), initial_cash=_D("-1")
        )


def test_funding_enabled_without_rate_rejected() -> None:
    cfg = _cfg(costs=CostsConfig(funding=True, borrow_apr=None))
    with pytest.raises(QuickBacktestInputError):
        _run(_cols(5), {}, cfg=cfg)


def test_strategy_future_peek_raises_lookahead() -> None:
    class _Peek:
        def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
            window.close(len(window))  # one past the current bar
            return None

    with pytest.raises(LookAheadError):
        run_quick_backtest(
            _cfg(), _cols(5), timeframe=Timeframe.M1, strategy=_Peek(), initial_cash=_CASH
        )


# --- failure injection ---------------------------------------------------------


def test_failure_injection_commission_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fault in the commission model must surface, not be swallowed into a fake fill."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("injected commission failure")

    monkeypatch.setattr(qf, "compute_commission", _boom)
    with pytest.raises(RuntimeError, match="injected commission failure"):
        _run(_cols(20), _orders(2))


def test_failure_injection_strategy_error_propagates() -> None:
    class _Broken:
        def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
            raise ValueError("injected strategy failure")

    with pytest.raises(ValueError, match="injected strategy failure"):
        run_quick_backtest(
            _cfg(), _cols(5), timeframe=Timeframe.M1, strategy=_Broken(), initial_cash=_CASH
        )


# --- gate red reproduction -------------------------------------------------------


def test_gate_red_broken_input_changes_result() -> None:
    """Assertions must catch corrupted input: a poisoned close changes final equity."""
    good = _run(_cols(30), _orders(3))
    bad_cols = _cols(30)
    bad_cols.close[-1] = bad_cols.close[-1] + _D("1000")
    bad = _run(bad_cols, _orders(3))
    assert good.final_equity != bad.final_equity
    assert good.bars == bad.bars == 30


def test_gate_red_deterministic_baseline_detects_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same input twice is identical; a perturbed commission model is detected as drift."""
    base = _run(_cols(40), _orders(4))
    assert _run(_cols(40), _orders(4)).cash == base.cash

    real = qf.compute_commission
    monkeypatch.setattr(qf, "compute_commission", lambda *a, **k: real(*a, **k) + _D("1"))
    assert _run(_cols(40), _orders(4)).cash != base.cash


# --- perf ----------------------------------------------------------------------


@pytest.mark.perf
def test_perf_run_quick_backtest_200_bars(perf_budget: PerfBudget) -> None:
    cols, plan = _cols(_BARS), _orders(20)
    perf_budget.assert_within(
        lambda: _run(cols, plan), budget_ms=_BUDGET_MS, n=7, label="quick_backtest 200 bars"
    )


@pytest.mark.perf
def test_perf_gate_red_virtual_delay_10x(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10x virtual delay (10x budget) on commission must trip the same budget assertion."""
    cols, plan = _cols(_BARS), _orders(20)
    reset_virtual_totals()
    monkeypatch.setattr(
        qf,
        "compute_commission",
        virtual_delayed_sync(qf.compute_commission, _BUDGET_MS * 10 / 1000),
    )
    with pytest.raises(AssertionError, match="budget"):
        perf_budget.assert_within(
            lambda: _run(cols, plan), budget_ms=_BUDGET_MS, n=3, label="injected"
        )
