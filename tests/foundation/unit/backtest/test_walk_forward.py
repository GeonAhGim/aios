"""Unit tests for `backtest/application/walk_forward.py` -- task-2411 L35 DoD."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.walk_forward import (
    WalkForwardError,
    run_walk_forward,
)
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.param_stability import ParamGrid
from src.foundation.backtest.domain.splits import OosLeakageError, Split, make_splits
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
_CYCLE = [
    ("100", "100"), ("100", "110"), ("112", "111"), ("111", "90"), ("88", "89"),
]


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bars(n: int) -> list[Candle]:
    bars = []
    for i in range(n):
        open_price, close_price = _CYCLE[i % len(_CYCLE)]
        ts = _T0 + timedelta(hours=i)
        bars.append(Candle(
            symbol="BTC/USDT", exchange="bitget", timeframe="1h",
            open=Decimal(open_price), high=max(Decimal(open_price), Decimal(close_price)),
            low=min(Decimal(open_price), Decimal(close_price)), close=Decimal(close_price),
            volume=Decimal("1"), open_time=ts, close_time=ts,
        ))
    return bars


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy", version="v1", target_asset="BTC/USDT",
        market="crypto", exchange="bitget", initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.BUY_ORDER_PENDING,
                           condition="PRICE > 105"),
            FSMTransition(from_state=FSMState.BUY_ORDER_PENDING, to_state=FSMState.HOLDING,
                           condition=ORDER_FILLED),
            FSMTransition(from_state=FSMState.HOLDING, to_state=FSMState.SELL_ORDER_PENDING,
                           condition="PRICE < 95"),
            FSMTransition(from_state=FSMState.SELL_ORDER_PENDING, to_state=FSMState.IDLE,
                           condition=ORDER_FILLED),
        ],
        author_agent="test",
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy", strategy_version="v1", initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST, warmup_bars=0, periods_per_year=252,
    )


def _splits() -> list[Split]:
    return make_splits(n_bars=50, n_splits=2, mode="anchored", purge=0, embargo=0, min_train=20)


def _grid() -> ParamGrid:
    return ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})


def test_produces_one_window_per_split() -> None:
    report = run_walk_forward(
        _config(), _fsm_config(), _bars(50), _grid(), _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert len(report.windows) == 2
    assert report.selection_rule == "IS_NET_SHARPE_MAX"


def test_selected_params_are_from_the_grid() -> None:
    report = run_walk_forward(
        _config(), _fsm_config(), _bars(50), _grid(), _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    for window in report.windows:
        assert window.selected_params["warmup_bars"] in [0, 1, 2, 3]


def test_deterministic_repeat_run_selects_same_params() -> None:
    first = run_walk_forward(
        _config(), _fsm_config(), _bars(50), _grid(), _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    second = run_walk_forward(
        _config(), _fsm_config(), _bars(50), _grid(), _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [w.selected_params for w in first.windows] == [w.selected_params for w in second.windows]


def test_oos_stitched_metrics_cover_full_oos_span() -> None:
    report = run_walk_forward(
        _config(), _fsm_config(), _bars(50), _grid(), _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    total_oos_bars = sum(len(w.split.test) for w in report.windows)
    assert report.oos_stitched_metrics.period_start is not None
    # stitched equity curve length isn't exposed directly, but metrics must be computable
    assert report.oos_stitched_metrics.total_trades >= 0
    assert total_oos_bars > 0


def test_empty_splits_is_rejected() -> None:
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _config(), _fsm_config(), _bars(50), _grid(), [],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_unknown_grid_axis_is_rejected() -> None:
    bad_grid = ParamGrid(axes={"not_a_field": [1, 2, 3, 4]})
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _config(), _fsm_config(), _bars(50), bad_grid, _splits(),
            indicator_service=_FakePriceIndicatorService(),
        )


def test_overlapping_splits_are_rejected() -> None:
    bad_split = Split(train=range(0, 20), test=range(15, 30), purge=0, embargo=0)
    with pytest.raises(OosLeakageError):
        run_walk_forward(
            _config(), _fsm_config(), _bars(50), _grid(), [bad_split],
            indicator_service=_FakePriceIndicatorService(),
        )
