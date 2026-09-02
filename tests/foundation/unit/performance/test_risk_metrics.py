"""변동성·MDD·Sharpe·Calmar — backtest.compute_metrics와 정의를 공유한다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6 (L46)."""
from __future__ import annotations

from decimal import Decimal

from src.foundation.performance.domain.risk_metrics import (
    annualized_vol,
    calmar,
    max_drawdown,
    period_returns,
    sharpe,
)


def test_period_returns_computes_simple_returns_and_skips_zero_base():
    values = [Decimal("100"), Decimal("110"), Decimal("0"), Decimal("50")]
    returns = period_returns(values)
    # (100->110)=0.1, (110->0)=-1(전액 손실, 유효한 값), (0->50)은 밑변 0이라 건너뜀
    assert returns == [Decimal("0.1"), Decimal("-1")]


def test_max_drawdown_finds_worst_peak_to_trough():
    values = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("150"), Decimal("135")]
    # 피크 120에서 90까지 낙폭 = (120-90)/120 = 0.25
    assert max_drawdown(values) == Decimal("0.25")


def test_max_drawdown_of_monotonic_rise_is_zero():
    assert max_drawdown([Decimal("100"), Decimal("110"), Decimal("120")]) == Decimal("0")


def test_max_drawdown_of_empty_series_is_none():
    assert max_drawdown([]) is None


def test_annualized_vol_needs_at_least_two_returns():
    assert annualized_vol([Decimal("0.01")], periods_per_year=252) is None


def test_sharpe_is_none_when_volatility_is_zero():
    """수익률이 전부 같으면(변동성 0) sharpe는 0으로 나눔을 만들지 않고 None."""
    returns = [Decimal("0.01"), Decimal("0.01"), Decimal("0.01")]
    assert sharpe(returns, rf=Decimal("0"), periods_per_year=252) is None


def test_calmar_divides_annualized_return_by_mdd():
    assert calmar(Decimal("0.2"), Decimal("0.1")) == Decimal("2")


def test_calmar_is_none_when_mdd_is_zero():
    assert calmar(Decimal("0.2"), Decimal("0")) is None
