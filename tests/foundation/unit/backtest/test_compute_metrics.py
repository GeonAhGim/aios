"""compute_metrics() 단위테스트."""
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.domain.models import EquityPoint, SimulatedFill

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


def test_empty_equity_curve_raises() -> None:
    with pytest.raises(ValueError):
        compute_metrics(
            equity_curve=[], fills=[], initial_equity=Decimal("100"), periods_per_year=252
        )


def test_total_return_and_max_drawdown_take_provided_peak() -> None:
    curve = [
        _point(0, "100", "0"),
        _point(1, "110", "0"),
        _point(2, "90", "18.18"),  # 미리 계산해 넣은 값 — equity_tracker 자체는 별도 테스트 대상
        _point(3, "120", "0"),
    ]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.total_return_pct == Decimal("20")
    assert metrics.max_drawdown_pct == Decimal("18.18")
    assert metrics.period_start == curve[0].timestamp
    assert metrics.period_end == curve[-1].timestamp


def test_sharpe_none_with_fewer_than_two_returns() -> None:
    curve = [_point(0, "100", "0"), _point(1, "101", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


def test_sharpe_computed_matches_manual_stdev() -> None:
    equities = ["100", "101", "99", "103", "100"]
    curve = [_point(i, e, "0") for i, e in enumerate(equities)]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    returns = [
        float((Decimal(b) - Decimal(a)) / Decimal(a))
        for a, b in zip(equities, equities[1:], strict=False)
    ]
    expected = statistics.mean(returns) / statistics.stdev(returns) * (252**0.5)
    assert metrics.sharpe_ratio is not None
    assert float(metrics.sharpe_ratio) == pytest.approx(expected)


def test_sortino_none_when_no_negative_returns() -> None:
    curve = [_point(i, e, "0") for i, e in enumerate(["100", "101", "102", "103"])]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.sortino_ratio is None


def test_win_rate_and_trade_count_from_round_trips() -> None:
    curve = [_point(0, "100", "0"), _point(1, "105", "0")]
    fills = [
        _fill(OrderSide.BUY, "100", qty="1", fee="1"),
        _fill(OrderSide.SELL, "110", qty="1", fee="1"),  # 승: (110-100)*1 - 2 = 8
        _fill(OrderSide.BUY, "100", qty="1", fee="1"),
        _fill(OrderSide.SELL, "95", qty="1", fee="1"),  # 패: (95-100)*1 - 2 = -7
    ]
    metrics = compute_metrics(
        equity_curve=curve, fills=fills, initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.total_trades == 2
    assert metrics.win_rate_pct == Decimal("50")


def test_win_rate_none_with_no_closed_trades() -> None:
    curve = [_point(0, "100", "0"), _point(1, "100", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.win_rate_pct is None
    assert metrics.total_trades == 0


def test_turnover_sums_notional_over_initial_equity() -> None:
    curve = [_point(0, "100", "0"), _point(1, "100", "0")]
    fills = [_fill(OrderSide.BUY, "100", qty="2"), _fill(OrderSide.SELL, "100", qty="2")]
    metrics = compute_metrics(
        equity_curve=curve, fills=fills, initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.turnover == Decimal("4")  # (200+200)/100
