"""Unit tests for `backtest/application/stress.py` -- task-2411 L35 DoD."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.stress import (
    REQUIRED_SCENARIOS,
    StressError,
    run_stress,
)
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))


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
        symbol="BTC/USDT", exchange="bitget", timeframe="1h",
        open=Decimal(open_price), high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)), close=Decimal(close_price),
        volume=Decimal("1"), open_time=ts, close_time=ts,
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
        cost_model=_COST, warmup_bars=0, periods_per_year=252,
    )


def test_all_required_scenarios_run_with_no_missing() -> None:
    report = run_stress(
        _config(), _fsm_config(), _bars(), list(REQUIRED_SCENARIOS),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert report.missing == []
    assert set(report.per_scenario.keys()) == set(REQUIRED_SCENARIOS)


def test_cost_x3_yields_worse_return_than_cost_x2() -> None:
    report = run_stress(
        _config(), _fsm_config(), _bars(), ["COST_X2", "COST_X3"],
        indicator_service=_FakePriceIndicatorService(),
    )
    x2 = report.per_scenario["COST_X2"]
    x3 = report.per_scenario["COST_X3"]
    assert x2.total_trades == x3.total_trades == 1  # 같은 신호, 비용만 다르다
    assert x3.total_return_pct < x2.total_return_pct


def test_worst_days_removed_shrinks_bar_count_effect() -> None:
    """모든 bar에서 신호가 나오도록 짧은 시나리오는 아니지만, 최소한 예외 없이
    5개 미만 bar에 대해서는 fail-closed로 거부되는지 확인."""
    with pytest.raises(StressError):
        run_stress(
            _config(), _fsm_config(), _bars()[:3], ["WORST_5_DAYS_REMOVED"],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(StressError):
        run_stress(
            _config(), _fsm_config(), _bars(), ["NOT_A_SCENARIO"],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_missing_lists_required_scenarios_not_run() -> None:
    report = run_stress(
        _config(), _fsm_config(), _bars(), ["COST_X2"],
        indicator_service=_FakePriceIndicatorService(),
    )
    assert "COST_X2" not in report.missing
    assert "GAP_2PCT" in report.missing
