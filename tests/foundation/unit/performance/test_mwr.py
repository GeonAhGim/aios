"""MWR(금액가중수익률, IRR 이분법) — 알려진 값과 수렴 실패 케이스.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.foundation.performance.domain.mwr import mwr

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=365)


def test_mwr_with_no_interim_cashflows_matches_simple_return():
    """현금흐름 없이 1000 -> 1100이면 IRR은 단순수익률 10%와 같아야 한다."""
    result = mwr([], Decimal("1000"), Decimal("1100"), _T0, _T1)

    assert result is not None
    assert abs(result - Decimal("0.1")) < Decimal("1E-8")


def test_mwr_with_known_deposit_matches_expected_irr():
    """1000 투자, 중간에 500 입금, 종료값 1650일 때의 IRR을 npv=0 재확인으로
    검증한다(폐형 공식 대신 결과를 직접 npv에 대입해 0에 가까운지 확인 —
    이분법 자체의 정답을 재현하는 회귀 테스트)."""
    from src.foundation.performance.domain.models import Cashflow, CashflowKind

    mid = _T0 + timedelta(days=182)
    cashflows = [Cashflow(at=mid, amount=Decimal("500"), kind=CashflowKind.DEPOSIT)]

    result = mwr(cashflows, Decimal("1000"), Decimal("1650"), _T0, _T1)

    assert result is not None
    t_mid = Decimal(182) / Decimal(365)
    npv = (
        -Decimal("1000")
        - Decimal("500") / (Decimal(1) + result) ** t_mid
        + Decimal("1650") / (Decimal(1) + result) ** Decimal(1)
    )
    assert abs(npv) < Decimal("1E-6")


def test_mwr_returns_none_when_period_has_zero_duration():
    assert mwr([], Decimal("1000"), Decimal("1100"), _T0, _T0) is None


def test_mwr_returns_none_when_start_value_is_not_positive():
    assert mwr([], Decimal("0"), Decimal("1100"), _T0, _T1) is None
