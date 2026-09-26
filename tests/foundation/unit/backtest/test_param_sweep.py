"""Unit tests for `backtest/application/param_sweep.py` -- task-2411 L35 DoD."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import src.foundation.backtest.application.param_sweep as param_sweep_module
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.param_sweep import SweepError, sweep
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.param_stability import ParamGrid
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _bars() -> list[Candle]:
    return [
        _bar(index=0, open_price="100", close_price="100"),
        _bar(index=1, open_price="100", close_price="110"),
        _bar(index=2, open_price="112", close_price="111"),
        _bar(index=3, open_price="111", close_price="90"),
        _bar(index=4, open_price="88", close_price="89"),
        _bar(index=5, open_price="90", close_price="91"),
        _bar(index=6, open_price="91", close_price="92"),
    ]


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=252,
    )


def _grid() -> ParamGrid:
    return ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})


def test_sweep_runs_one_point_per_grid_point_in_order() -> None:
    result = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        n_blocks=2,
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [p.point for p in result.points] == [(0,), (1,), (2,), (3,)]
    assert len(result.perf_matrix) == 4
    assert all(len(row) == 2 for row in result.perf_matrix)


def test_sweep_is_deterministic() -> None:
    first = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        indicator_service=_FakePriceIndicatorService(),
    )
    second = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [p.metrics for p in first.points] == [p.metrics for p in second.points]


def test_unknown_grid_axis_is_rejected() -> None:
    bad_grid = ParamGrid(axes={"not_a_field": [1, 2, 3, 4]})
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            bad_grid,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_too_many_blocks_for_equity_curve_is_rejected() -> None:
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            n_blocks=100,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_zero_n_blocks_is_rejected() -> None:
    """n_blocks < 1 is not a valid slicing of an equity curve -- fail-closed input."""
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            n_blocks=0,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_run_backtest_failure_propagates_without_partial_result(monkeypatch) -> None:
    """A dependency failure (`run_backtest` raising) must not be swallowed into a
    partial/empty `SweepResult` -- fail-closed, not fail-open."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("run_backtest dependency failure (injected)")

    monkeypatch.setattr(param_sweep_module, "run_backtest", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            indicator_service=_FakePriceIndicatorService(),
        )


def test_sweep_throughput_meets_minimum_points_per_second() -> None:
    """Numeric performance assertion (D2 floor, ADR-2026-09-09-C): a 4-point grid over
    7 bars with a fake indicator service must clear a generous throughput floor -- this
    guards against an accidental O(n^2) regression in the grid-point loop, not a tight
    production SLA."""
    grid = _grid()
    started = time.perf_counter()
    result = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        grid,
        indicator_service=_FakePriceIndicatorService(),
    )
    elapsed = time.perf_counter() - started
    throughput = len(result.points) / elapsed if elapsed > 0 else float("inf")
    assert throughput > 10.0, f"grid-point throughput too low: {throughput:.2f} points/sec"
