"""회계 항등식 검사 — 잔차/PENDING.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.domain.identity import _require, check_identity
from src.foundation.performance.domain.models import Cashflow, CashflowKind, ComponentBreakdown

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=30)


def _breakdown(**overrides) -> ComponentBreakdown:
    defaults = dict(
        gross_pnl=Decimal("120"),
        fees=Decimal("10"),
        slippage=Decimal("5"),
        funding=Decimal("2"),
        fx=Decimal("0"),
        cashflows_net=Decimal("0"),
        estimated_tax=Decimal("3"),
        net_pnl=Decimal("100"),
    )
    defaults.update(overrides)
    return ComponentBreakdown(**defaults)


def test_identity_holds_when_both_equations_balance():
    breakdown = _breakdown()  # 120-10-5-2+0-3 = 100 = net_pnl
    cashflows = [Cashflow(at=_T0, amount=Decimal("1000"), kind=CashflowKind.DEPOSIT)]

    result = check_identity(
        breakdown, start_value=Decimal("0"), end_value=Decimal("1100"), cashflows=cashflows
    )

    assert result.ok is True
    assert result.residual == Decimal("0")
    assert result.pending_fields == ()


def test_missing_component_is_pending_not_zero():
    breakdown = _breakdown(fx=None)

    result = check_identity(
        breakdown, start_value=Decimal("0"), end_value=Decimal("1100"), cashflows=[]
    )

    assert result.ok is False
    assert result.residual is None
    assert "fx" in result.pending_fields


def test_mismatched_breakdown_reports_nonzero_residual():
    breakdown = _breakdown(net_pnl=Decimal("999"))  # 계산값 100과 불일치

    result = check_identity(
        breakdown, start_value=Decimal("0"), end_value=Decimal("1000"), cashflows=[]
    )

    assert result.ok is False
    assert result.residual is not None
    assert result.residual != 0


def test_mismatched_end_valuation_reports_nonzero_residual():
    breakdown = _breakdown()  # net_pnl=100은 스스로 일관됨
    cashflows = [Cashflow(at=_T0, amount=Decimal("1000"), kind=CashflowKind.DEPOSIT)]

    result = check_identity(
        breakdown, start_value=Decimal("0"), end_value=Decimal("9999"), cashflows=cashflows
    )

    assert result.ok is False
    assert result.residual is not None
    assert result.residual != 0


def test_require_raises_on_unexpected_none():
    """negative — `_require()`는 pending 검사를 통과한 뒤에도 `None`이면 조용히
    0으로 대체하지 않고 불변식 위반을 예외로 드러낸다(task-1759가 `# type:
    ignore` 3건을 대체한 분기, 그 이후 직접 겨냥한 테스트가 없었다)."""
    with pytest.raises(ValueError, match="net_pnl.*None"):
        _require(None, "net_pnl")
