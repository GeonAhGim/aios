"""BT-12 build_tearsheet() 단위테스트.

DoD (a) 결정론: 같은 BacktestResult로 두 번 build_tearsheet()를 호출하면
model_dump_json() 결과가 바이트 단위로 같다.
DoD (b) 수치 exact: 고정 입력에 대해 compute_metrics()가 낸 값을 그대로
포맷만 했는지(재계산하지 않았는지)를 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.trading import OrderSide
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
    SimulatedFill,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _point(i: int, equity: str, drawdown: str) -> EquityPoint:
    return EquityPoint(
        bar_index=i,
        timestamp=_T0 + timedelta(hours=i),
        equity=Decimal(equity),
        drawdown_pct=Decimal(drawdown),
    )


def _fill(
    side: OrderSide, price: str, qty: str = "1", fee: str = "0", slip: str = "0"
) -> SimulatedFill:
    return SimulatedFill(
        bar_index=0,
        timestamp=_T0,
        symbol="BTC/USDT",
        side=side,
        price=Decimal(price),
        quantity=Decimal(qty),
        fee=Decimal(fee),
        slippage_cost=Decimal(slip),
    )


def _config(initial_equity: str = "10000") -> BacktestConfig:
    return BacktestConfig(
        strategy_id="strat-1",
        strategy_version="v1",
        initial_equity=Decimal(initial_equity),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        warmup_bars=0,
        periods_per_year=252,
    )


def _result(
    equities: list[str], fills: list[SimulatedFill] | None = None, initial_equity: str = "10000"
) -> BacktestResult:
    config = _config(initial_equity)
    curve = [_point(i, e, "0") for i, e in enumerate(equities)]
    fills = fills or []
    metrics = compute_metrics(
        equity_curve=curve,
        fills=fills,
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    return BacktestResult(config=config, fills=fills, equity_curve=curve, metrics=metrics)


def test_schema_version_is_v1() -> None:
    result = _result(["10000", "11000"])
    view = build_tearsheet(result, currency="USDT", precision=2)
    assert view.schema_version == "v1" == SCHEMA_VERSION


def test_determinism_byte_identical_json() -> None:
    result = _result(
        ["10000", "10100", "9900", "10300", "10000"],
        fills=[
            _fill(OrderSide.BUY, "100", qty="1", fee="1"),
            _fill(OrderSide.SELL, "110", qty="1", fee="1"),
        ],
    )
    first = build_tearsheet(result, currency="USDT", precision=2)
    second = build_tearsheet(result, currency="USDT", precision=2)
    assert first.model_dump_json() == second.model_dump_json()


def test_exact_numeric_matches_compute_metrics_without_reimplementation() -> None:
    curve_equities = ["10000", "11000"]
    result = _result(curve_equities)
    view = build_tearsheet(result, currency="USDT", precision=2)

    assert view.initial_equity.amount == Decimal("10000")
    assert view.initial_equity.currency == "USDT"
    assert view.initial_equity.precision == 2
    assert view.initial_equity.state == "FINAL"
    assert view.final_equity.amount == Decimal("11000")
    assert view.total_return.value_pct == result.metrics.total_return_pct
    assert view.total_return.value_pct == Decimal("10")
    assert view.total_return.basis == "NET"
    assert view.total_return.method == "TWR"
    assert view.total_return.annualized is False
    assert view.total_return.periods_per_year == 252
    assert view.risk["mdd_pct"] == result.metrics.max_drawdown_pct
    assert view.total_trades == result.metrics.total_trades
    assert view.turnover == result.metrics.turnover


def test_none_metrics_are_not_replaced_with_zero() -> None:
    # 표본 2개 미만 -> compute_metrics()는 sharpe/sortino를 None으로 낸다.
    result = _result(["10000", "10100"])
    view = build_tearsheet(result, currency="USDT", precision=2)
    assert view.risk["sharpe"] is None
    assert view.risk["sortino"] is None
    assert view.win_rate_pct is None


def test_period_bounds_match_equity_curve() -> None:
    result = _result(["10000", "10500", "10200"])
    view = build_tearsheet(result, currency="USDT", precision=2)
    assert view.period_start == result.equity_curve[0].timestamp
    assert view.period_end == result.equity_curve[-1].timestamp
    assert view.initial_equity.as_of == view.period_start
    assert view.final_equity.as_of == view.period_end


def test_warnings_are_passed_through() -> None:
    config = _config()
    curve = [_point(0, "10000", "0"), _point(1, "10100", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=config.initial_equity, periods_per_year=252
    )
    result = BacktestResult(
        config=config,
        fills=[],
        equity_curve=curve,
        metrics=metrics,
        warnings=["W_COST_MODEL_ZERO"],
    )
    view = build_tearsheet(result, currency="USDT", precision=2)
    assert view.warnings == ["W_COST_MODEL_ZERO"]


def test_returns_tearsheet_view_type() -> None:
    result = _result(["10000", "10100"])
    view = build_tearsheet(result, currency="USDT", precision=2)
    assert isinstance(view, TearsheetView)
