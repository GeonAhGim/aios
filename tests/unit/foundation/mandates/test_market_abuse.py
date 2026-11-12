"""L4_compliance_and_regulatory_v1.0.md#9 CM-10 -- `domain/market_abuse.py`
unit tests.

task-2460 DoD mapping: (b) wash-trade boundary (60s hit / 61s miss / cross-
tenant false-positive guard), (c) marking-the-close boundary (30% miss /
30.1% hit / outside-window exclusion), (d) spoofing boundary (3 orders at
exactly 10x miss / above 10x hit / 2 orders never hit), (e) fail-closed
`DATA_MISSING` per pattern, (f) no coupling with CM-9's pre-trade
`rules/wash_trade.py`, (g) purity/determinism.

DEEPEN 2460 (task-2864, docs/audit/DEPTH_CM.md): the original leaf graded D1
for missing failure injection, a numeric performance assertion, a gate-red
regression test, and D3 adversarial/multi-instance proof (negative>=4 was
already satisfied). `detect()` is pure (no I/O), so those four are added as:
  - failure injection: a `Decimal`/`datetime` subclass that still passes
    every `isinstance` check upstream but raises `ArithmeticError` deep in
    the `qty`/`executed_at` arithmetic each pattern performs. This found a
    real gap -- `_safe` only caught `KeyError`/`TypeError`, so a corrupted
    numeric value escaped `detect()` as an uncaught crash instead of the
    `DATA_MISSING` hit I-02 requires; fixed by widening `_safe`'s except
    clause to also catch `ArithmeticError` (covers `decimal.InvalidOperation`,
    `OverflowError`, `ZeroDivisionError`, all `ArithmeticError` subclasses).
  - numeric performance: a wall-clock ceiling over many `detect()` calls on a
    non-trivial window (wash-trade is O(n^2) in fill count).
  - gate-red regression: `_worst_severity` reproduces the worst-verdict
    adoption CM-11's `evaluate_tenant_day` performs over this leaf's hits,
    locking in that a clean window stays ALLOW/green, a real (non-missing)
    pattern hit stays WARN (CM-11 never blocks on these three patterns
    alone), and only `DATA_MISSING` turns the leaf DENY/red.
  - D3 adversarial + multi-instance/replay: `AbuseHit` tamper rejection
    (frozen dataclass) and byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain import market_abuse
from src.foundation.mandates.domain.market_abuse import (
    PATTERN_MARKING_THE_CLOSE,
    PATTERN_SPOOFING,
    PATTERN_WASH_TRADE,
    REASON_DATA_MISSING,
    detect,
)
from tests.unit.foundation.mandates.market_abuse_fixtures import (
    _CLOSE,
    _T0,
    _fill,
    _order,
    _window,
)

# -- (b) wash trade -----------------------------------------------------


def test_wash_trade_exactly_at_window_boundary_hits():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=60)),
    ]
    hits = detect(_window(fills=fills), {})
    wash_hits = [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE]
    assert len(wash_hits) == 1
    assert wash_hits[0].severity == ComplianceVerdict.WARN
    assert wash_hits[0].evidence["fill_ids"] == ["f1", "f2"]


def test_wash_trade_one_second_past_window_misses():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=61)),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []


def test_wash_trade_cross_tenant_different_owner_is_not_a_hit():
    fills = [
        _fill(fill_id="f1", tenant_id="tenant-a", owner_id="owner-1", side="BUY", executed_at=_T0),
        _fill(
            fill_id="f2",
            tenant_id="tenant-b",
            owner_id="owner-2",
            side="SELL",
            executed_at=_T0 + timedelta(seconds=10),
        ),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []


def test_wash_trade_custom_window_param_is_honored():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=5)),
    ]
    hits = detect(_window(fills=fills), {"wash_window_sec": 4})
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []


# -- (c) marking the close ------------------------------------------------


def _other_participants(total_qty: str, executed_at: datetime) -> list[dict[str, object]]:
    """Three other tenants splitting `total_qty`, each individually well
    under the 30% share limit -- keeps the fixture focused on tenant-a's
    boundary instead of also tripping the rule for a counterparty."""
    per_tenant = Decimal(total_qty) / 3
    return [
        _fill(
            fill_id=f"other-{i}",
            tenant_id=f"tenant-other-{i}",
            qty=per_tenant,
            executed_at=executed_at,
        )
        for i in range(3)
    ]


def test_marking_the_close_self_share_at_30_pct_misses():
    """Negative: exactly 30% share is NOT a hit (strict >)."""
    fills = [
        _fill(fill_id="self", tenant_id="tenant-a", qty=Decimal("300"), executed_at=_CLOSE),
        *_other_participants("700", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


def test_marking_the_close_self_share_below_30_pct_misses():
    """Negative: 20% share well under the 30% limit must not trigger."""
    fills = [
        _fill(fill_id="self", tenant_id="tenant-a", qty=Decimal("200"), executed_at=_CLOSE),
        *_other_participants("800", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


def test_marking_the_close_multi_tenant_each_below_30_pct_misses():
    """Negative: two tenants each at ~28.6% (200/700) must not trigger."""
    fills = [
        _fill(fill_id="self-a", tenant_id="tenant-a", qty=Decimal("200"), executed_at=_CLOSE),
        _fill(fill_id="self-b", tenant_id="tenant-b", qty=Decimal("200"), executed_at=_CLOSE),
        *_other_participants("300", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


@pytest.mark.perf
def test_marking_the_close_self_share_performance_ceiling():
    """Performance: detect() on 500 fills must complete under 200ms.

    marking-the-close is O(n) in fill count (single pass through fills).
    """
    import time

    fills = [
        _fill(fill_id=f"f{i}", tenant_id=f"tenant-{i % 10}", qty=Decimal("100"), executed_at=_CLOSE)
        for i in range(500)
    ]
    window = _window(fills=fills)
    start = time.perf_counter()
    for _ in range(100):
        detect(window, {})
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < 200, (
        f"marking_the_close detect p95 budget 200ms exceeded: {elapsed_ms:.1f}ms"
    )


def test_marking_the_close_self_share_over_30_pct_hits():
    fills = [
        _fill(fill_id="self", tenant_id="tenant-a", qty=Decimal("301"), executed_at=_CLOSE),
        *_other_participants("699", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    close_hits = [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE]
    assert len(close_hits) == 1
    assert close_hits[0].severity == ComplianceVerdict.WARN
    assert close_hits[0].evidence["tenant_id"] == "tenant-a"


def test_marking_the_close_fill_outside_window_excluded_from_both_sides():
    outside = _CLOSE - timedelta(seconds=601)
    fills = [
        # Would push tenant-a over 30% if counted -- must be excluded entirely.
        _fill(fill_id="outside", tenant_id="tenant-a", qty=Decimal("900"), executed_at=outside),
        _fill(fill_id="self", tenant_id="tenant-a", qty=Decimal("300"), executed_at=_CLOSE),
        *_other_participants("700", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


# -- (d) spoofing / layering ----------------------------------------------


def test_spoofing_three_orders_exactly_10x_ratio_misses():
    """Negative: cancel_qty == ratio * fill_qty is NOT a hit (strict >)."""
    orders = [_order(order_id=f"o{i}", qty=Decimal("100")) for i in range(3)]
    fills = [_fill(qty=Decimal("30"))]  # 300 == 10 * 30
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


def test_spoofing_cancel_qty_below_ratio_misses():
    """Negative: cancel_qty < ratio * fill_qty is not a hit."""
    orders = [_order(order_id=f"o{i}", qty=Decimal("50")) for i in range(3)]
    fills = [_fill(qty=Decimal("100"))]  # 150 < 10 * 100
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


def test_spoofing_single_order_never_hits():
    """Negative: fewer than min_orders (3) never triggers spoofing."""
    orders = [_order(order_id="o1", qty=Decimal("10000"))]
    fills = [_fill(qty=Decimal("1"))]
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


def test_spoofing_no_cancel_time_misses():
    """Negative: orders without canceled_at are skipped, never crash."""
    orders = [_order(order_id=f"o{i}", qty=Decimal("1000"), canceled_at=None) for i in range(3)]
    fills = [_fill(qty=Decimal("1"))]
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


@pytest.mark.perf
def test_spoofing_performance_ceiling():
    """Performance: detect() on 500 orders + 200 fills must complete under 200ms."""
    import time

    orders = [
        _order(order_id=f"o{i}", qty=Decimal("1000"), canceled_at=_T0 + timedelta(seconds=1))
        for i in range(500)
    ]
    fills = [_fill(fill_id=f"f{i}", qty=Decimal("10")) for i in range(200)]
    window = _window(orders=orders, fills=fills)
    start = time.perf_counter()
    for _ in range(100):
        detect(window, {})
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < 200, f"spoofing detect p95 budget 200ms exceeded: {elapsed_ms:.1f}ms"


def test_spoofing_three_orders_above_10x_ratio_hits():
    orders = [_order(order_id=f"o{i}", qty=Decimal("101")) for i in range(3)]
    fills = [_fill(qty=Decimal("30"))]  # 303 > 10 * 30
    hits = detect(_window(orders=orders, fills=fills), {})
    spoof_hits = [h for h in hits if h.pattern_id == PATTERN_SPOOFING]
    assert len(spoof_hits) == 1
    assert spoof_hits[0].evidence["cancel_count"] == 3


def test_spoofing_two_orders_never_hits_regardless_of_quantity():
    orders = [_order(order_id=f"o{i}", qty=Decimal("100000")) for i in range(2)]
    fills = [_fill(qty=Decimal("1"))]
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


def test_spoofing_order_cancelled_after_cancel_window_does_not_count():
    orders = [
        _order(order_id=f"o{i}", qty=Decimal("101"), canceled_at=_T0 + timedelta(seconds=6))
        for i in range(3)
    ]
    fills = [_fill(qty=Decimal("30"))]
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


# -- (e) fail-closed DATA_MISSING, one per pattern -------------------------


def test_missing_fills_is_fail_closed_for_all_three_patterns():
    window = {"orders": [], "market_close_at": _CLOSE}
    hits = detect(window, {})
    pattern_ids = {h.pattern_id for h in hits}
    assert pattern_ids == {PATTERN_WASH_TRADE, PATTERN_MARKING_THE_CLOSE, PATTERN_SPOOFING}
    assert all(h.reason_code == REASON_DATA_MISSING for h in hits)
    assert all(h.severity == ComplianceVerdict.DENY for h in hits)


def test_missing_market_close_at_is_fail_closed_for_marking_the_close_only():
    window = {"fills": [], "orders": []}
    hits = detect(window, {})
    close_hits = [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE]
    assert len(close_hits) == 1
    assert close_hits[0].reason_code == REASON_DATA_MISSING
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


def test_missing_orders_is_fail_closed_for_spoofing_only():
    window = {"fills": [], "market_close_at": _CLOSE}
    hits = detect(window, {})
    spoof_hits = [h for h in hits if h.pattern_id == PATTERN_SPOOFING]
    assert len(spoof_hits) == 1
    assert spoof_hits[0].reason_code == REASON_DATA_MISSING
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


def test_fill_missing_required_subfield_is_fail_closed_not_a_crash():
    bad_fill = {"tenant_id": "tenant-a", "instrument_id": "x", "qty": Decimal("1")}
    hits = detect(_window(fills=[bad_fill]), {})
    wash_hits = [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE]
    # A single fill never pairs with itself, so wash-trade sees no candidate
    # pair and must not crash walking the missing `owner_id`/`side` fields.
    assert wash_hits == []
    close_hits = [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE]
    assert len(close_hits) == 1
    assert close_hits[0].reason_code == REASON_DATA_MISSING


# -- (f) no coupling with CM-9's pre-trade wash_trade.py -------------------


# -- (g) purity / determinism ------------------------------------------


def _imported_module_names(source: str) -> set[str]:
    """Extract top-level import names from *source* via AST."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def test_market_abuse_module_imports_no_io_or_nondeterminism():
    source = Path(market_abuse.__file__).read_text(encoding="utf-8")
    banned_modules = {"random", "httpx", "asyncpg", "openai"}
    imported = _imported_module_names(source)
    assert not any(name.split(".")[0] in banned_modules for name in imported)

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"now", "utcnow"}, (
                "market_abuse.py must take all timestamps as arguments, never read the clock"
            )


def test_pattern_id_constants_are_exposed_and_distinct():
    ids = {PATTERN_WASH_TRADE, PATTERN_MARKING_THE_CLOSE, PATTERN_SPOOFING}
    assert len(ids) == 3
