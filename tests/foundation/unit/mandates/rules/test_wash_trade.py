"""L4_compliance_and_regulatory_v1.0.md#9 CM-9 — `domain/rules/wash_trade.py`
unit tests.

task-2458 DoD mapping: (c) same-tenant crossing-price DENY + no-cross ALLOW
+ cross-tenant negative (no false positive), (d) missing `open_orders` is
fail-closed DENY, (e) static purity/determinism.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import short_sale, wash_trade

_FORBIDDEN_IMPORTS = ("datetime", "random", "httpx", "asyncpg", "openai")
_NOW = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)

_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_INSTRUMENT = "BTC/USDT"


def _open_order(tenant_id: str, side: str, price: Decimal, instrument: str = _INSTRUMENT) -> dict:
    return {"tenant_id": tenant_id, "instrument": instrument, "side": side, "price": price}


def test_crossing_opposite_order_same_tenant_denies():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.rule_id == wash_trade.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["tenant_id"] == _TENANT_A


def test_non_crossing_opposite_order_same_tenant_passes():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10010"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_crossing_opposite_order_different_tenant_passes():
    """교차 테넌트 오탐 방지 — 서로 다른 tenant_id의 반대 주문은 자전거래가
    아니다(진짜 상대방)."""
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_B, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_same_side_open_order_is_never_a_wash_trade():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "SELL", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_different_instrument_is_never_a_wash_trade():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"), instrument="ETH/USDT")],
    }
    assert wash_trade.check({}, snapshot) is None


def test_missing_open_orders_is_fail_closed_deny():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("1"),
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "snapshot.open_orders"


def test_missing_tenant_id_is_fail_closed_deny():
    snapshot = {
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("1"),
        "open_orders": [],
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_check_is_pure_and_deterministic():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) == wash_trade.check({}, snapshot)


def test_short_sale_and_wash_trade_register_in_a_bundle_and_both_evaluate():
    """CM-9 (f) — wiring here stops at registering both new rules in a
    `RuleBundle`; `application/evaluate_pre_trade.py` (CM-8) and order paths
    are untouched by this leaf. This proves `evaluate_bundle` actually
    invokes both registered `rule_id`s (both hit, given inputs crafted to
    trigger each) rather than asserting on production wiring that does not
    exist yet."""
    bundle = RuleBundle(
        version="v1",
        rules=(
            RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),
            RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),
        ),
    )
    snapshot = {
        # triggers short_sale: naked short (excess 1, no locate)
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
        # triggers wash_trade: crosses own open buy at the same price
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }

    decision = evaluate_bundle(bundle, snapshot, now=_NOW)

    hit_rule_ids = {hit.rule_id for hit in decision.rule_hits}
    assert hit_rule_ids == {short_sale.RULE_ID, wash_trade.RULE_ID}
    assert decision.verdict == ComplianceVerdict.DENY


def test_module_imports_no_clock_random_or_io_libraries():
    """CM-A2 — static proof that this rule cannot be non-deterministic or
    perform I/O: no `datetime`/`random`/`httpx`/`asyncpg`/`openai` import."""
    source = Path(wash_trade.__file__).read_text(encoding="utf-8")
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
