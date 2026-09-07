"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — `domain/rules/position_limit.py`
unit tests.

task-2066 DoD mapping: (1) three exact-Decimal boundary points, (4) missing
input is fail-closed DENY.
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules import position_limit

_MAX_POSITION = Decimal("50000.00")


def test_position_limit_exactly_at_limit_passes():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.00")}
    assert position_limit.check(params, snapshot) is None


def test_position_limit_one_tick_over_limit_denies():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.01")}
    hit = position_limit.check(params, snapshot)
    assert hit is not None
    assert hit.rule_id == position_limit.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["projected_position_notional"] == "50000.01"
    assert hit.evidence["max_position_notional"] == "50000.00"


def test_position_limit_below_limit_passes():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("49999.99")}
    assert position_limit.check(params, snapshot) is None


def test_position_limit_missing_param_is_fail_closed_deny():
    hit = position_limit.check({}, {"projected_position_notional": Decimal("1.00")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "max_position_notional"


def test_position_limit_missing_snapshot_field_is_fail_closed_deny():
    hit = position_limit.check({"max_position_notional": _MAX_POSITION}, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "projected_position_notional"


def test_position_limit_non_decimal_input_is_fail_closed_deny():
    hit = position_limit.check(
        {"max_position_notional": 50000.0},
        {"projected_position_notional": Decimal("1.0")},
    )
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_position_limit_check_is_pure_and_deterministic():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.01")}
    assert position_limit.check(params, snapshot) == position_limit.check(params, snapshot)
