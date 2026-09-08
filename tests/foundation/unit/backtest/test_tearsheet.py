"""build_tearsheet() 단위테스트 — BT-10 compute_metrics()가 이미 계산한
BacktestMetrics를 재계산 없이 그대로 옮겨 담는지, performance 계약
(PerformanceMethodologyView/methodology_hash())을 제대로 재사용하는지,
그리고 스냅샷이 결정론적인지(같은 입력 → 바이트 동일 JSON)를 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.foundation.backtest.application.tearsheet import build_tearsheet
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    CostModel,
)
from src.foundation.performance.domain.methodology import methodology_hash
from src.foundation.performance.domain.models import Methodology

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


def _result(*, periods_per_year: int = 252, warnings: list[str] | None = None) -> BacktestResult:
    config = BacktestConfig(
        strategy_id="strat-1",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=periods_per_year,
    )
    metrics = BacktestMetrics(
        period_start=_T0,
        period_end=_T1,
        total_return_pct=Decimal("12.5"),
        max_drawdown_pct=Decimal("3.25"),
        sharpe_ratio=Decimal("1.1"),
        sortino_ratio=Decimal("1.4"),
        win_rate_pct=Decimal("55"),
        total_trades=4,
        turnover=Decimal("0.5"),
    )
    return BacktestResult(
        config=config,
        fills=[],
        equity_curve=[],
        metrics=metrics,
        warnings=warnings or [],
    )


def test_build_tearsheet_reuses_metrics_without_recomputing() -> None:
    result = _result()
    view = build_tearsheet(result)

    assert view.schema_version == "v1"
    assert view.strategy_id == "strat-1"
    assert view.strategy_version == "v1"
    assert view.initial_equity == Decimal("1000")
    # BT-10이 이미 계산한 값 그대로여야 한다(재계산이면 여기서 값이 달라진다).
    assert view.metrics.total_return_pct == result.metrics.total_return_pct
    assert view.metrics.max_drawdown_pct == result.metrics.max_drawdown_pct
    assert view.metrics.sharpe_ratio == result.metrics.sharpe_ratio
    assert view.metrics.sortino_ratio == result.metrics.sortino_ratio
    assert view.metrics.win_rate_pct == result.metrics.win_rate_pct
    assert view.metrics.total_trades == result.metrics.total_trades
    assert view.metrics.turnover == result.metrics.turnover
    assert view.metrics.period_start == result.metrics.period_start
    assert view.metrics.period_end == result.metrics.period_end


def test_build_tearsheet_methodology_matches_reused_contract_hash() -> None:
    """`methodology_hash()`를 새로 만들지 않고 재사용하므로, 같은 필드값에
    대해 독립적으로 호출해도 같은 해시가 나와야 한다."""
    result = _result(periods_per_year=365)
    view = build_tearsheet(result)

    expected = Methodology(
        version=view.methodology.version,
        methodology_hash="",
        twr_method=view.methodology.twr_method,
        mwr_method=view.methodology.mwr_method,
        risk_free_rate_pct=view.methodology.risk_free_rate_pct,
        periods_per_year=365,
    )
    assert view.methodology.periods_per_year == 365
    assert view.methodology.methodology_hash == methodology_hash(expected)
    assert view.methodology.schema_version == "v1"


def test_build_tearsheet_carries_warnings() -> None:
    result = _result(warnings=["warmup_bars가 0입니다"])
    view = build_tearsheet(result)
    assert view.warnings == ["warmup_bars가 0입니다"]


def test_build_tearsheet_is_deterministic_json_snapshot() -> None:
    """(a) 결정론 DoD — 같은 백테스트 결과로 두 번 build_tearsheet()를
    호출하면 model_dump_json() 결과가 바이트 단위로 동일해야 한다."""
    result = _result()

    first = build_tearsheet(result).model_dump_json()
    second = build_tearsheet(result).model_dump_json()

    assert first == second


def test_build_tearsheet_decimal_fields_serialize_as_strings() -> None:
    result = _result()
    view = build_tearsheet(result)
    dumped = view.model_dump(mode="json")

    assert dumped["initial_equity"] == "1000"
    assert dumped["metrics"]["total_return_pct"] == "12.5"
    assert dumped["metrics"]["sharpe_ratio"] == "1.1"
    assert dumped["methodology"]["risk_free_rate_pct"] == "0"
