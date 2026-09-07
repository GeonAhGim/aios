"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — `domain/rules/liquidity.py`
unit tests.

task-2066 DoD mapping: (1) three exact-Decimal boundary points, (2) a
negative test with concrete average-daily-traded-value numbers, (4) missing
input / non-positive ADV is fail-closed DENY.
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules import liquidity

_MAX_PCT = Decimal("10.00")
_ADV = Decimal("1000000.00")  # average daily traded value


def test_liquidity_exactly_at_limit_passes():
    # order_notional / ADV * 100 == 10.00% exactly
    snapshot = {"order_notional": Decimal("100000.00"), "average_daily_traded_value": _ADV}
    assert liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot) is None


def test_liquidity_one_tick_over_limit_denies_with_concrete_numbers():
    """DoD item 2 — a single order exceeding X% of ADV denies, with concrete
    figures: a $1,000,000.00 ADV instrument capped at 10.00% of ADV rejects
    a $100,100.00 order (10.01% of ADV, one 0.01-point tick over)."""
    order_notional = Decimal("100100.00")
    snapshot = {"order_notional": order_notional, "average_daily_traded_value": _ADV}
    hit = liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot)
    assert hit is not None
    assert hit.rule_id == liquidity.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["order_notional"] == "100100.00"
    assert hit.evidence["average_daily_traded_value"] == "1000000.00"
    assert hit.evidence["observed_pct_of_adv"] == "10.0100"
    assert hit.evidence["max_pct_of_adv"] == "10.00"


def test_liquidity_below_limit_passes():
    snapshot = {"order_notional": Decimal("99999.00"), "average_daily_traded_value": _ADV}
    assert liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot) is None


def test_liquidity_missing_param_is_fail_closed_deny():
    snapshot = {"order_notional": Decimal("1.00"), "average_daily_traded_value": _ADV}
    hit = liquidity.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "max_pct_of_adv"


def test_liquidity_missing_order_notional_is_fail_closed_deny():
    snapshot = {"average_daily_traded_value": _ADV}
    hit = liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "order_notional"


def test_liquidity_missing_adv_is_fail_closed_deny():
    snapshot = {"order_notional": Decimal("1.00")}
    hit = liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "average_daily_traded_value"


def test_liquidity_zero_adv_is_fail_closed_deny_not_division_by_zero():
    snapshot = {"order_notional": Decimal("1.00"), "average_daily_traded_value": Decimal("0")}
    hit = liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_liquidity_negative_adv_is_fail_closed_deny():
    snapshot = {"order_notional": Decimal("1.00"), "average_daily_traded_value": Decimal("-5.00")}
    hit = liquidity.check({"max_pct_of_adv": _MAX_PCT}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_liquidity_check_is_pure_and_deterministic():
    snapshot = {"order_notional": Decimal("100100.00"), "average_daily_traded_value": _ADV}
    params = {"max_pct_of_adv": _MAX_PCT}
    assert liquidity.check(params, snapshot) == liquidity.check(params, snapshot)
