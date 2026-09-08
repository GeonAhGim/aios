"""build_tearsheet() 단위테스트 — BT-12.

결정론(같은 결과=바이트 동일 model_dump_json())과, compute_metrics()가 낸
값을 재계산 없이 그대로 옮기는지를 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.application.tearsheet import (
    SCHEMA_VERSION,
    TearsheetView,
    build_tearsheet,
)
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestResult,
    CostModel,
    EquityPoint,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _point(i: int, equity: str, drawdown: str) -> EquityPoint:
    return EquityPoint(
        bar_index=i,
        timestamp=_T0 + timedelta(hours=i),
        equity=Decimal(equity),
        drawdown_pct=Decimal(drawdown),
    )


def _config(
    *, initial_equity: Decimal = Decimal("1000"), periods_per_year: int = 252
) -> BacktestConfig:
    return BacktestConfig(
        strategy_id="strat-1",
        strategy_version="v1.0.0",
        initial_equity=initial_equity,
        cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
        warmup_bars=0,
        periods_per_year=periods_per_year,
    )


def _result(equities: list[str], *, initial_equity: str = "1000") -> BacktestResult:
    curve = [_point(i, e, "0") for i, e in enumerate(equities)]
    config = _config(initial_equity=Decimal(initial_equity))
    metrics = compute_metrics(
        equity_curve=curve,
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    return BacktestResult(config=config, fills=[], equity_curve=curve, metrics=metrics)


def test_schema_version_is_v1() -> None:
    result = _result(["1000", "1100", "1200"])
    view = build_tearsheet(result)
    assert view.schema_version == "v1" == SCHEMA_VERSION


def test_determinism_byte_identical_json() -> None:
    result = _result(["1000", "1050", "990", "1200"])
    first = build_tearsheet(result).model_dump_json()
    second = build_tearsheet(result).model_dump_json()
    assert first == second


def test_metrics_are_passed_through_without_recompute() -> None:
    result = _result(["1000", "1100", "1200"])
    view = build_tearsheet(result)
    assert view.total_return_pct == result.metrics.total_return_pct == Decimal("20")
    assert view.max_drawdown_pct == result.metrics.max_drawdown_pct
    assert view.sharpe_ratio == result.metrics.sharpe_ratio
    assert view.sortino_ratio == result.metrics.sortino_ratio
    assert view.win_rate_pct == result.metrics.win_rate_pct
    assert view.total_trades == result.metrics.total_trades
    assert view.turnover == result.metrics.turnover
    assert view.initial_equity == Decimal("1000")
    assert view.final_equity == Decimal("1200")


def test_none_metrics_pass_through_as_none() -> None:
    # 표본 부족 -> sharpe/sortino None, 체결 없음 -> win_rate_pct None(46번 §2 원칙)
    result = _result(["1000", "1010"])
    view = build_tearsheet(result)
    assert view.sharpe_ratio is None
    assert view.sortino_ratio is None
    assert view.win_rate_pct is None


def test_methodology_view_reuses_performance_contract() -> None:
    result = _result(["1000", "1100", "1200"], initial_equity="1000")
    view = build_tearsheet(result)
    assert view.methodology.version == "pm-v1"
    assert view.methodology.periods_per_year == result.config.periods_per_year
    assert view.methodology.schema_version == "v1"
    assert len(view.methodology.methodology_hash) == 64  # sha256 hexdigest


def test_decimal_fields_serialize_as_strings() -> None:
    result = _result(["1000", "1100", "1200"])
    dumped = build_tearsheet(result).model_dump(mode="json")
    assert isinstance(dumped["total_return_pct"], str)
    assert isinstance(dumped["initial_equity"], str)
    assert isinstance(dumped["final_equity"], str)


def test_warnings_and_ids_pass_through() -> None:
    curve = [_point(0, "1000", "0"), _point(1, "1100", "0")]
    config = _config()
    metrics = compute_metrics(
        equity_curve=curve,
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    result = BacktestResult(
        config=config,
        fills=[],
        equity_curve=curve,
        metrics=metrics,
        warnings=["표본 부족"],
    )
    view = build_tearsheet(result)
    assert isinstance(view, TearsheetView)
    assert view.warnings == ["표본 부족"]
    assert view.strategy_id == "strat-1"
    assert view.strategy_version == "v1.0.0"
