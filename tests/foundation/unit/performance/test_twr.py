"""TWR(시간가중수익률) — 기간연결(현금흐름 기초 반영) 정확값.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.domain.models import Cashflow, CashflowKind
from src.foundation.performance.domain.twr import MissingInputError, twr

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=15)
_T2 = _T0 + timedelta(days=30)


def test_twr_links_two_subperiods_with_a_deposit_at_the_boundary():
    """1000 -> (10%) -> 1100, 그 시점에 200 입금(원금 1300) -> (10%) -> 1430.
    연결 수익률 = 1.1*1.1-1 = 0.21."""
    valuations = [(_T0, Decimal("1000")), (_T1, Decimal("1100")), (_T2, Decimal("1430"))]
    cashflows = [Cashflow(at=_T1, amount=Decimal("200"), kind=CashflowKind.DEPOSIT)]

    result = twr(valuations, cashflows)

    assert result == Decimal("0.21")


def test_twr_with_no_cashflows_is_simple_return():
    valuations = [(_T0, Decimal("1000")), (_T2, Decimal("1100"))]
    assert twr(valuations, []) == Decimal("0.1")


def test_twr_withdrawal_reduces_base():
    """400 출금 후 원금 = 1100-400=700, 700 -> 770(10%)."""
    valuations = [(_T0, Decimal("1000")), (_T1, Decimal("1100")), (_T2, Decimal("770"))]
    cashflows = [Cashflow(at=_T1, amount=Decimal("400"), kind=CashflowKind.WITHDRAWAL)]

    result = twr(valuations, cashflows)

    assert result == Decimal("0.21")


def test_twr_rejects_cashflow_without_matching_valuation():
    """현금흐름 시점에 평가액이 없으면 근사하지 않고 명시적으로 거부한다."""
    valuations = [(_T0, Decimal("1000")), (_T2, Decimal("1100"))]
    unmatched_time = _T0 + timedelta(days=7)
    cashflows = [Cashflow(at=unmatched_time, amount=Decimal("50"), kind=CashflowKind.DEPOSIT)]

    with pytest.raises(MissingInputError) as excinfo:
        twr(valuations, cashflows)
    assert excinfo.value.reason_code == "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


def test_twr_requires_at_least_two_valuations():
    with pytest.raises(MissingInputError):
        twr([(_T0, Decimal("1000"))], [])
