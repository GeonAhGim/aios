"""build_tearsheet() 단위테스트 — BT-12 DoD(결정론·수치 exact)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.foundation.backtest.application.tearsheet import build_tearsheet
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    CostModel,
    EquityPoint,
)
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY, methodology_hash

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _config(*, periods_per_year: int = 252) -> BacktestConfig:
    return BacktestConfig(
        strategy_id="strat-1",
        strategy_version="v1.0.0",
        initial_equity=Decimal("1000"),
        cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
        warmup_bars=0,
        periods_per_year=periods_per_year,
    )


def _metrics() -> BacktestMetrics:
    return BacktestMetrics(
        period_start=_T0,
        period_end=_T0 + timedelta(days=1),
        total_return_pct=Decimal("12.34"),
        max_drawdown_pct=Decimal("5.00"),
        sharpe_ratio=Decimal("1.2345"),
        sortino_ratio=Decimal("1.5"),
        win_rate_pct=Decimal("60"),
        total_trades=3,
        turnover=Decimal("2.5"),
    )


def _result(*, periods_per_year: int = 252) -> BacktestResult:
    equity_curve = [
        EquityPoint(bar_index=0, timestamp=_T0, equity=Decimal("1000"), drawdown_pct=Decimal("0"))
    ]
    return BacktestResult(
        config=_config(periods_per_year=periods_per_year),
        fills=[],
        equity_curve=equity_curve,
        metrics=_metrics(),
        warnings=["표본 부족"],
    )


def test_deterministic_json_across_two_calls() -> None:
    result = _result()
    first = build_tearsheet(result).model_dump_json()
    second = build_tearsheet(result).model_dump_json()
    assert first == second


def test_metrics_are_passed_through_without_recomputation() -> None:
    result = _result()
    view = build_tearsheet(result)
    metrics = result.metrics
    assert view.total_return_pct == metrics.total_return_pct
    assert view.max_drawdown_pct == metrics.max_drawdown_pct
    assert view.sharpe_ratio == metrics.sharpe_ratio
    assert view.sortino_ratio == metrics.sortino_ratio
    assert view.win_rate_pct == metrics.win_rate_pct
    assert view.total_trades == metrics.total_trades
    assert view.turnover == metrics.turnover
    assert view.period_start == metrics.period_start
    assert view.period_end == metrics.period_end


def test_schema_version_and_decimal_string_serialization() -> None:
    view = build_tearsheet(_result())
    dumped = view.model_dump(mode="json")
    assert dumped["schema_version"] == "v1"
    assert dumped["total_return_pct"] == "12.34"
    assert dumped["max_drawdown_pct"] == "5.00"
    assert dumped["sharpe_ratio"] == "1.2345"
    assert dumped["turnover"] == "2.5"


def test_methodology_reuses_default_when_periods_per_year_matches() -> None:
    view = build_tearsheet(_result(periods_per_year=DEFAULT_METHODOLOGY.periods_per_year))
    assert view.methodology.methodology_hash == DEFAULT_METHODOLOGY.methodology_hash
    assert view.methodology.version == DEFAULT_METHODOLOGY.version


def test_methodology_hash_recomputed_via_shared_function_when_periods_differ() -> None:
    custom_periods = 365 * 24
    view = build_tearsheet(_result(periods_per_year=custom_periods))
    from dataclasses import replace

    expected_provisional = replace(
        DEFAULT_METHODOLOGY, periods_per_year=custom_periods, methodology_hash=""
    )
    expected_hash = methodology_hash(expected_provisional)
    assert view.methodology.periods_per_year == custom_periods
    assert view.methodology.methodology_hash == expected_hash
    assert view.methodology.methodology_hash != DEFAULT_METHODOLOGY.methodology_hash


def test_warnings_and_fill_count_are_carried_over() -> None:
    view = build_tearsheet(_result())
    assert view.warnings == ["표본 부족"]
    assert view.total_fills == 0
