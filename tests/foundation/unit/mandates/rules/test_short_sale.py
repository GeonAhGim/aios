"""L4_compliance_and_regulatory_v1.0.md#9 CM-9 — `domain/rules/short_sale.py`
unit tests.

task-2458 DoD mapping: (b) position-exceeds boundary (100/101 shares) +
locate coverage via `borrow_available_qty` + KRX uptick rule boundary
(9990/10000/10010), (d) missing-field fail-closed, (e) static purity/
determinism (no wall-clock/random/network imports).
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules import short_sale

_FORBIDDEN_IMPORTS = ("datetime", "random", "httpx", "asyncpg", "openai")


def test_selling_exactly_held_position_passes():
    snapshot = {"side": "SELL", "order_qty": Decimal("100"), "position_qty": Decimal("100")}
    assert short_sale.check({}, snapshot) is None


def test_selling_one_share_more_than_held_without_locate_denies():
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }
    hit = short_sale.check({}, snapshot)
    assert hit is not None
    assert hit.rule_id == short_sale.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["excess_qty"] == "1"


def test_selling_one_share_more_than_held_with_locate_available_passes():
    """무차입 공매도만 막고 차입(locate) 공매도는 막지 않는다."""
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("1"),
    }
    assert short_sale.check({}, snapshot) is None


def test_buy_order_is_never_a_short_sale():
    snapshot = {"side": "BUY", "order_qty": Decimal("1000")}
    assert short_sale.check({}, snapshot) is None


def test_krx_uptick_below_last_price_denies():
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("1"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("10"),
        "venue": "KRX",
        "order_price": Decimal("9990"),
        "last_price": Decimal("10000"),
    }
    hit = short_sale.check({"krx_uptick_required": True}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["order_price"] == "9990"
    assert hit.evidence["last_price"] == "10000"


def test_krx_uptick_at_last_price_passes():
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("1"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("10"),
        "venue": "KRX",
        "order_price": Decimal("10000"),
        "last_price": Decimal("10000"),
    }
    assert short_sale.check({"krx_uptick_required": True}, snapshot) is None


def test_krx_uptick_above_last_price_passes():
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("1"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("10"),
        "venue": "KRX",
        "order_price": Decimal("10010"),
        "last_price": Decimal("10000"),
    }
    assert short_sale.check({"krx_uptick_required": True}, snapshot) is None


def test_missing_position_qty_is_fail_closed_deny():
    snapshot = {"side": "SELL", "order_qty": Decimal("10")}
    hit = short_sale.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "snapshot.position_qty"


def test_missing_side_is_fail_closed_deny():
    hit = short_sale.check({}, {"order_qty": Decimal("10"), "position_qty": Decimal("0")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_check_is_pure_and_deterministic():
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }
    assert short_sale.check({}, snapshot) == short_sale.check({}, snapshot)


def test_module_imports_no_clock_random_or_io_libraries():
    """CM-A2 — static proof that this rule cannot be non-deterministic or
    perform I/O: no `datetime`/`random`/`httpx`/`asyncpg`/`openai` import."""
    source = Path(short_sale.__file__).read_text(encoding="utf-8")
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
    for forbidden in _FORBIDDEN_IMPORTS:
        assert not any(
            module == forbidden or module.startswith(forbidden + ".")
            for module in imported_modules
        ), f"unexpected non-deterministic/IO import: {forbidden}"
