"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — `domain/rules/leverage.py`
unit tests.

task-2066 DoD mapping: (1) three exact-Decimal boundary points, (4) missing
input is fail-closed DENY. Item (3) — not reimplementing risk R-06/R-07 — is
enforced by `leverage.py` importing nothing from `src.core.risk`; asserted
here as a static import check so a future edit cannot silently reintroduce
the coupling.
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules import leverage

_MAX_LEVERAGE = Decimal("3.00")


def test_leverage_exactly_at_limit_passes():
    params = {"max_leverage": _MAX_LEVERAGE}
    snapshot = {"projected_gross_leverage": Decimal("3.00")}
    assert leverage.check(params, snapshot) is None


def test_leverage_one_tick_over_limit_denies():
    params = {"max_leverage": _MAX_LEVERAGE}
    snapshot = {"projected_gross_leverage": Decimal("3.01")}
    hit = leverage.check(params, snapshot)
    assert hit is not None
    assert hit.rule_id == leverage.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["projected_gross_leverage"] == "3.01"
    assert hit.evidence["max_leverage"] == "3.00"


def test_leverage_below_limit_passes():
    params = {"max_leverage": _MAX_LEVERAGE}
    snapshot = {"projected_gross_leverage": Decimal("2.99")}
    assert leverage.check(params, snapshot) is None


def test_leverage_missing_param_is_fail_closed_deny():
    hit = leverage.check({}, {"projected_gross_leverage": Decimal("1.00")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "max_leverage"


def test_leverage_missing_snapshot_field_is_fail_closed_deny():
    hit = leverage.check({"max_leverage": _MAX_LEVERAGE}, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "projected_gross_leverage"


def test_leverage_non_decimal_input_is_fail_closed_deny():
    # A float/str slipping in must deny, not be silently coerced into a
    # limit comparison (task-2066 DoD item 4).
    hit = leverage.check({"max_leverage": 3.0}, {"projected_gross_leverage": Decimal("1.0")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_leverage_rule_does_not_import_risk_axis():
    """task-2066 DoD item 3 — this compliance rule must not reimplement or
    depend on the risk axis's R-06/R-07 leverage rule (INVARIANTS.md I-09:
    the two are independent authorities)."""
    source = Path(leverage.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert not any(module.startswith("src.core.risk") for module in imported_modules)


def test_leverage_check_is_pure_and_deterministic():
    params = {"max_leverage": _MAX_LEVERAGE}
    snapshot = {"projected_gross_leverage": Decimal("3.01")}
    first = leverage.check(params, snapshot)
    second = leverage.check(params, snapshot)
    assert first == second
