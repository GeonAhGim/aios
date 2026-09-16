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


# --- D2 증빙 보강(task-3356, L31 gross/net 분리) ---------------------------
#
# compute_metrics.py 모듈 docstring이 증명하는 항등식
# (gross_equity == net_equity + total_fees + total_slippage)을 fee/slippage가
# 있는 구체적 픽스처로 대조한다 — 함수 docstring의 대수적 주장이 실제
# 구현과 어긋나지 않는지가 이 신규 로직의 핵심 회귀 지점이다.


def test_gross_return_exceeds_net_by_exactly_fees_plus_slippage() -> None:
    curve = [_point(0, "1000", "0"), _point(1, "1050", "0")]
    fills = [
        _fill(OrderSide.BUY, "100", qty="10", fee="5", slip="3"),
        _fill(OrderSide.SELL, "110", qty="10", fee="6", slip="4"),
    ]
    metrics = compute_metrics(
        equity_curve=curve, fills=fills, initial_equity=Decimal("1000"), periods_per_year=252
    )
    assert metrics.net_return_pct == metrics.total_return_pct
    assert metrics.total_fees == Decimal("11")
    assert metrics.total_slippage == Decimal("7")
    expected_gross = metrics.net_return_pct + Decimal("18") / Decimal("1000") * 100
    assert metrics.gross_return_pct == expected_gross


def test_total_fees_and_slippage_zero_with_no_fills() -> None:
    """실패 주입 성격의 음(negative) 경계 — fill이 하나도 없으면 비용
    합계가 조용히 None이 아니라 0이어야 한다(체결이 없다는 사실 자체는
    "계산 불가"가 아니라 "비용이 0"이라는 확정 정보이기 때문)."""
    curve = [_point(0, "100", "0"), _point(1, "100", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.total_fees == Decimal("0")
    assert metrics.total_slippage == Decimal("0")
    assert metrics.gross_return_pct == metrics.net_return_pct == Decimal("0")


def test_total_funding_stays_none_not_zero() -> None:
    """N/A 사유: Phase 1 simulate_fill()은 펀딩비를 적용하지 않는다(BT-8
    domain/costs/funding.py가 별도 모듈로 존재하나 체결 루프에 배선되지
    않음) — 이 실패 모드는 이 leaf 범위에서 재현 불가능하므로, 조용히
    0을 내는 대신 명시적으로 None을 단언해 "미계산"을 고정한다."""
    curve = [_point(0, "100", "0"), _point(1, "100", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.total_funding is None


def test_calmar_none_when_drawdown_is_zero() -> None:
    """음(negative) 경계 — 낙폭이 0이면 calmar(수익/낙폭)는 0으로 나누기라
    조용히 0이나 무한대를 내는 대신 None이어야 한다."""
    curve = [_point(0, "100", "0"), _point(1, "110", "0"), _point(2, "120", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.max_drawdown_pct == Decimal("0")
    assert metrics.calmar_ratio is None


def test_calmar_positive_when_return_and_drawdown_both_present() -> None:
    curve = [
        _point(0, "100", "0"),
        _point(1, "130", "0"),
        _point(2, "110", "15.38"),
    ]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.calmar_ratio is not None
    assert metrics.calmar_ratio > 0


def test_exposure_time_none_with_single_bar() -> None:
    """음(negative) 경계 — bar가 1개뿐이면 "간격"이 정의되지 않아 exposure는
    0으로 위장하지 않고 None이어야 한다(sharpe/sortino의 표본부족 None과
    동일 원칙)."""
    curve = [_point(0, "100", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=252
    )
    assert metrics.exposure_time_pct is None


def test_exposure_time_counts_open_position_through_final_bar() -> None:
    """진입 후 아직 청산되지 않은 포지션(run 종료 시점까지 열린 채)도
    exposure에 포함돼야 한다 — 청산 fill이 없다고 exposure를 0으로 깔면
    "계속 보유 중"이라는 사실이 사라진다."""
    curve = [_point(i, "100", "0") for i in range(5)]  # bar 0..4, 간격 4개
    fills = [_fill(OrderSide.BUY, "100", qty="1")]
    fills[0] = fills[0].model_copy(update={"bar_index": 1})
    metrics = compute_metrics(
        equity_curve=curve, fills=fills, initial_equity=Decimal("100"), periods_per_year=252
    )
    # entry bar_index=1, 마지막 bar_index=4 → exposed=3, 전체 간격=4 → 75%
    assert metrics.exposure_time_pct == Decimal("75")


def test_annualization_field_echoes_periods_per_year() -> None:
    curve = [_point(0, "100", "0"), _point(1, "101", "0")]
    metrics = compute_metrics(
        equity_curve=curve, fills=[], initial_equity=Decimal("100"), periods_per_year=365
    )
    assert metrics.annualization == 365
