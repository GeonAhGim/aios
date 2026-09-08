"""BT-12 deterministic tearsheet tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.application.tearsheet import (
    EmptyEquityCurveError,
    build_tearsheet,
)
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    CostModel,
    EquityPoint,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _result(equities: list[str]) -> BacktestResult:
    config = BacktestConfig(
        strategy_id="strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        warmup_bars=0,
        periods_per_year=252,
    )
    curve = [
        EquityPoint(
            bar_index=index,
            timestamp=_T0 + timedelta(hours=index),
            equity=Decimal(equity),
            drawdown_pct=Decimal("0"),
        )
        for index, equity in enumerate(equities)
    ]
    metrics = BacktestMetrics(
        period_start=_T0,
        period_end=curve[-1].timestamp if curve else _T0,
        total_return_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        sharpe_ratio=None,
        sortino_ratio=None,
        win_rate_pct=None,
        total_trades=0,
        turnover=Decimal("0"),
    )
    return BacktestResult(config=config, fills=[], equity_curve=curve, metrics=metrics, warnings=[])


def test_tearsheet_is_deterministic_and_uses_exact_decimal_values() -> None:
    result = _result(["1000"] + ["1010"] * 8 + ["1100"])

    first = build_tearsheet(result)
    second = build_tearsheet(result)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.total_return == Decimal("0.1")
    assert first.max_drawdown == Decimal("0")


def test_tearsheet_returns_none_for_unavailable_statistics() -> None:
    report = build_tearsheet(_result(["1000"]))

    assert report.sharpe is None
    assert report.sortino is None
    assert report.win_rate is None


def test_empty_equity_curve_fails_closed() -> None:
    with pytest.raises(EmptyEquityCurveError):
        build_tearsheet(_result([]))
