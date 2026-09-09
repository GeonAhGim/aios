"""build_tearsheet() 단위테스트 — BT-12.

지표 값 자체(Sharpe 산식 등)는 test_compute_metrics.py가 이미 검증한다.
여기서는 "compute_metrics()가 낸 BacktestMetrics를 리포트 뷰로 그대로
옮기는가", "결정론(같은 입력=같은 출력)", "빈 equity_curve 거부"만 본다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.application.tearsheet import build_tearsheet
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestResult,
    CostModel,
    EquityPoint,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _point(i: int, equity: str, drawdown: str = "0") -> EquityPoint:
    return EquityPoint(
        bar_index=i,
        timestamp=_T0 + timedelta(hours=i),
        equity=Decimal(equity),
        drawdown_pct=Decimal(drawdown),
    )


def _config(**overrides: object) -> BacktestConfig:
    fields: dict[str, object] = {
        "strategy_id": "strat-1",
        "strategy_version": "v1",
        "initial_equity": Decimal("100"),
        "cost_model": CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        "warmup_bars": 0,
        "periods_per_year": 252,
    }
    fields.update(overrides)
    return BacktestConfig(**fields)  # type: ignore[arg-type]


def _result(
    curve: list[EquityPoint], *, config: BacktestConfig, warnings: list[str] | None = None
) -> BacktestResult:
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    return BacktestResult(
        config=config, fills=[], equity_curve=curve, metrics=metrics,
        warnings=warnings or [],
    )


def test_empty_equity_curve_raises() -> None:
    config = _config()
    metrics = compute_metrics(
        equity_curve=[_point(0, "100")], fills=[], initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    result = BacktestResult(config=config, fills=[], equity_curve=[], metrics=metrics)
    with pytest.raises(ValueError):
        build_tearsheet(result)


def test_report_reuses_metrics_without_recomputing() -> None:
    curve = [_point(0, "100"), _point(1, "110"), _point(2, "120")]
    config = _config()
    result = _result(curve, config=config)

    view = build_tearsheet(result)

    assert view.strategy_id == "strat-1"
    assert view.strategy_version == "v1"
    assert view.initial_equity == Decimal("100")
    assert view.final_equity == Decimal("120")
    assert view.total_return.value_pct == result.metrics.total_return_pct
    assert view.total_return.basis == "NET"
    assert view.total_return.method == "TWR"
    assert view.total_return.periods_per_year == 252
    assert view.max_drawdown_pct == result.metrics.max_drawdown_pct
    assert view.sharpe_ratio == result.metrics.sharpe_ratio
    assert view.sortino_ratio == result.metrics.sortino_ratio
    assert view.win_rate_pct == result.metrics.win_rate_pct
    assert view.total_trades == result.metrics.total_trades
    assert view.turnover == result.metrics.turnover
    assert view.basis == "PAPER_SIM"
    assert view.config_hash == config.config_hash()
    assert view.schema_version == "v1"


def test_sharpe_none_passes_through_unfilled() -> None:
    curve = [_point(0, "100"), _point(1, "101")]  # 표본 2개 미만 → compute_metrics가 None
    result = _result(curve, config=_config())

    view = build_tearsheet(result)

    assert view.sharpe_ratio is None
    assert view.sortino_ratio is None
    assert view.win_rate_pct is None


def test_limitations_pass_through_warnings() -> None:
    curve = [_point(0, "100"), _point(1, "100")]
    result = _result(curve, config=_config(), warnings=["zero-cost model"])

    view = build_tearsheet(result)

    assert view.limitations == ["zero-cost model"]


def test_same_input_yields_identical_snapshot() -> None:
    curve = [_point(0, "100"), _point(1, "105"), _point(2, "95")]
    config = _config()
    result_a = _result(curve, config=config)
    result_b = _result(list(curve), config=config)

    view_a = build_tearsheet(result_a)
    view_b = build_tearsheet(result_b)

    assert view_a.model_dump_json() == view_b.model_dump_json()


def test_decimal_fields_serialize_as_strings() -> None:
    curve = [_point(0, "100"), _point(1, "110")]
    result = _result(curve, config=_config())

    payload = build_tearsheet(result).model_dump(mode="json")

    assert isinstance(payload["initial_equity"], str)
    assert isinstance(payload["final_equity"], str)
    assert isinstance(payload["turnover"], str)
    assert isinstance(payload["total_return"]["value_pct"], str)
