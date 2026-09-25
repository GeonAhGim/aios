"""회계 항등식 검사 — 잔차/PENDING.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD).

`_require()` 관련 테스트(task-3191 DEEPEN task-1759 PLT-44, commit b3957486)
— pending 사전검사 이후에도 `None`이면 마지막 방어선으로 예외를 던지는
헬퍼인데, `check_identity()`를 통한 정상 경로에서는 pending 검사가 항상
먼저 걸러내므로 그 raise 분기가 한 번도 실행되지 않았다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.domain import identity
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


# ---------- _require(): 마지막 방어선 ----------


def test_require_returns_value_when_present():
    assert _require(Decimal("5"), "gross_pnl") == Decimal("5")


def test_require_raises_when_none():
    """negative — pending 검사를 통과했는데도 None이면(호출자 불변식 위반)
    조용히 0으로 대체하지 않고 즉시 예외로 드러낸다."""
    with pytest.raises(ValueError, match="gross_pnl"):
        _require(None, "gross_pnl")


@pytest.mark.parametrize("field", ["fees", "slippage", "net_pnl"])
def test_require_error_message_names_the_offending_field(field: str):
    with pytest.raises(ValueError, match=field):
        _require(None, field)


def test_pending_field_drift_is_still_caught_by_require_as_last_defense(monkeypatch):
    """실패주입 — `_BREAKDOWN_FIELDS`가 실제 필드 목록과 어긋나면(예: 향후
    필드 추가/개명 중 하나를 pending 목록에 반영하는 걸 빠뜨림) pending
    사전검사가 그 필드의 `None`을 놓친다. 이때도 `_require`가 마지막
    방어선으로 예외를 던져야 한다 — 0 대체나 `TypeError`(`None - Decimal`)로
    새는 대신."""
    monkeypatch.setattr(
        identity,
        "_BREAKDOWN_FIELDS",
        tuple(name for name in identity._BREAKDOWN_FIELDS if name != "fx"),
    )
    breakdown = _breakdown(fx=None)

    with pytest.raises(ValueError, match="fx"):
        check_identity(breakdown, start_value=Decimal("0"), end_value=Decimal("1100"), cashflows=[])
