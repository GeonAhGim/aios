"""16.1 단위테스트 — 순수 상한 검증 로직."""
from decimal import Decimal

import pytest

from src.core.loader.risk_policy_loader import StrategyAllocationPolicy
from src.services.capital_allocation import (
    CapitalAllocationError,
    allocation_cap_pct,
    validate_capital_allocation,
)

POLICY = StrategyAllocationPolicy(unverified_max_pct=10.0, certified_level4_max_pct=25.0)


def test_unverified_strategy_uses_lower_cap():
    assert allocation_cap_pct(False, POLICY) == Decimal("10.0")


def test_certified_strategy_uses_higher_cap():
    assert allocation_cap_pct(True, POLICY) == Decimal("25.0")


def test_allocation_within_unverified_cap_passes():
    validate_capital_allocation(
        Decimal("1000"), Decimal("10000"), certified_badge=False, policy=POLICY
    )  # 10% 정확히 — 초과 아님


def test_allocation_exceeding_unverified_cap_rejected():
    with pytest.raises(CapitalAllocationError):
        validate_capital_allocation(
            Decimal("1500"), Decimal("10000"), certified_badge=False, policy=POLICY
        )


def test_same_allocation_passes_for_certified_but_not_unverified():
    # 15% — 미인증(10%)은 거부, 인증(25%)은 통과
    with pytest.raises(CapitalAllocationError):
        validate_capital_allocation(
            Decimal("1500"), Decimal("10000"), certified_badge=False, policy=POLICY
        )
    validate_capital_allocation(
        Decimal("1500"), Decimal("10000"), certified_badge=True, policy=POLICY
    )


def test_error_message_reports_exact_excess_and_cap():
    with pytest.raises(CapitalAllocationError) as exc_info:
        validate_capital_allocation(
            Decimal("1500"), Decimal("10000"), certified_badge=False, policy=POLICY
        )
    message = str(exc_info.value)
    assert "1000.00" in message  # 상한 금액(10%)
    assert "500.00" in message  # 초과분


def test_zero_balance_rejected():
    with pytest.raises(CapitalAllocationError):
        validate_capital_allocation(
            Decimal("100"), Decimal("0"), certified_badge=True, policy=POLICY
        )


def test_non_positive_allocation_rejected():
    with pytest.raises(CapitalAllocationError):
        validate_capital_allocation(
            Decimal("0"), Decimal("10000"), certified_badge=True, policy=POLICY
        )
