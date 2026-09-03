"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-09 — strategy_allocation 규칙 테스트.

DoD: 분모는 available_balance가 아니라 total_equity다.
"""
from decimal import Decimal

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import EquityInputs
from src.core.risk.rules import strategy_allocation
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs


def _inputs(
    *,
    certified_badge: bool | None,
    allocated_capital: str | None,
    total_equity: str | None,
) -> object:
    return sample_inputs(
        certified_badge=certified_badge,
        allocated_capital=(
            Decimal(allocated_capital) if allocated_capital is not None else None
        ),
        equity=EquityInputs(
            total_equity=Decimal(total_equity) if total_equity is not None else None, as_of=NOW
        ),
    )


def test_certified_allow_at_boundary():
    inputs = _inputs(certified_badge=True, allocated_capital="2500", total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_certified_deny_above_boundary():
    inputs = _inputs(certified_badge=True, allocated_capital="3000", total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_STRATEGY_ALLOCATION_EXCEEDED"


def test_unverified_allow_at_boundary():
    inputs = _inputs(certified_badge=False, allocated_capital="1000", total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_unverified_deny_above_boundary():
    inputs = _inputs(certified_badge=False, allocated_capital="1500", total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY


def test_denominator_is_total_equity_not_available_balance():
    # available_balance가 훨씬 작아도(예: 100) total_equity(10000) 기준으로
    # 판정한다 — available_balance는 이 규칙의 입력이 아니다.
    inputs = sample_inputs(
        certified_badge=True,
        allocated_capital=Decimal("2500"),
        equity=EquityInputs(
            total_equity=Decimal("10000"), available_balance=Decimal("100"), as_of=NOW
        ),
    )
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_missing_certified_badge_denies():
    inputs = _inputs(certified_badge=None, allocated_capital="1000", total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("certified_badge",)


def test_missing_allocated_capital_denies():
    inputs = _inputs(certified_badge=True, allocated_capital=None, total_equity="10000")
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("allocated_capital",)


def test_missing_total_equity_denies():
    inputs = _inputs(certified_badge=True, allocated_capital="1000", total_equity=None)
    result = strategy_allocation.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.total_equity",)
