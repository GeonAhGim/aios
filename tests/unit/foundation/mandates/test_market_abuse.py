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
import dataclasses
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain import market_abuse
from src.foundation.mandates.domain.market_abuse import (
    PATTERN_MARKING_THE_CLOSE,
    PATTERN_SPOOFING,
    PATTERN_WASH_TRADE,
    REASON_DATA_MISSING,
    AbuseHit,
    detect,
)

_T0 = datetime(2026, 1, 5, 5, 0, 0, tzinfo=timezone.utc)
_CLOSE = datetime(2026, 1, 5, 6, 0, 0, tzinfo=timezone.utc)


def _fill(**kwargs: object) -> dict[str, object]:
    base = {
        "fill_id": "f1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "owner_id": "owner-1",
        "side": "BUY",
        "qty": Decimal("10"),
        "executed_at": _T0,
    }
    base.update(kwargs)
    return base


def _order(**kwargs: object) -> dict[str, object]:
    base = {
        "order_id": "o1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "qty": Decimal("10"),
        "submitted_at": _T0,
        "canceled_at": _T0 + timedelta(seconds=1),
    }
    base.update(kwargs)
    return base


def _window(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {"fills": [], "orders": [], "market_close_at": _CLOSE}
    base.update(kwargs)
    return base


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
    fills = [
        _fill(fill_id="self", tenant_id="tenant-a", qty=Decimal("300"), executed_at=_CLOSE),
        *_other_participants("700", _CLOSE),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE] == []


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
    orders = [_order(order_id=f"o{i}", qty=Decimal("100")) for i in range(3)]
    fills = [_fill(qty=Decimal("30"))]  # 300 == 10 * 30
    hits = detect(_window(orders=orders, fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_SPOOFING] == []


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


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def test_market_abuse_does_not_import_or_share_logic_with_wash_trade_rule():
    source = Path(market_abuse.__file__).read_text(encoding="utf-8")
    imported = _imported_module_names(source)
    assert not any("wash_trade" in name for name in imported)

    pre_trade_wash_trade = Path(market_abuse.__file__).parent / "rules" / "wash_trade.py"
    if pre_trade_wash_trade.exists():
        # CM-9 (task-2458) had not landed on main as of this leaf; once it
        # has, also assert the pre-trade rule does not reach back into this
        # post-trade module.
        other_imported = _imported_module_names(pre_trade_wash_trade.read_text(encoding="utf-8"))
        assert not any("market_abuse" in name for name in other_imported)


# -- (g) purity / determinism ------------------------------------------


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


def test_detect_is_pure_and_deterministic():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)),
    ]
    window = _window(fills=fills)
    first = detect(window, {})
    second = detect(window, {})
    assert first == second


def test_pattern_id_constants_are_exposed_and_distinct():
    ids = {PATTERN_WASH_TRADE, PATTERN_MARKING_THE_CLOSE, PATTERN_SPOOFING}
    assert len(ids) == 3


# -- Failure injection (DEEPEN) -------------------------------------------


class _ExplodingDecimal(Decimal):
    """A `Decimal` subclass that still passes `isinstance(value, Decimal)`
    but raises on arithmetic -- simulates a corrupted numeric type slipping
    past every type guard and into `qty` accumulation."""

    def __add__(self, other: object) -> Decimal:
        raise ArithmeticError("simulated corrupted Decimal arithmetic")

    __radd__ = __add__


class _ExplodingDatetime(datetime):
    """A `datetime` subclass that still passes `isinstance(value, datetime)`
    but raises on subtraction -- simulates a corrupted timestamp slipping
    into the wash-trade delta-seconds calculation."""

    def __sub__(self, other: object) -> Any:
        raise ArithmeticError("simulated corrupted datetime arithmetic")


def test_wash_trade_corrupted_executed_at_fails_closed_not_a_crash():
    fills = [
        _fill(
            fill_id="f1",
            side="BUY",
            executed_at=_ExplodingDatetime(2026, 1, 5, 5, 0, 0, tzinfo=timezone.utc),
        ),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=1)),
    ]
    hits = detect(_window(fills=fills), {})
    wash_hits = [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE]
    assert len(wash_hits) == 1
    assert wash_hits[0].reason_code == REASON_DATA_MISSING
    assert wash_hits[0].severity == ComplianceVerdict.DENY


def test_marking_the_close_corrupted_qty_fails_closed_not_a_crash():
    fills = [
        _fill(
            fill_id="self", tenant_id="tenant-a", qty=_ExplodingDecimal("300"), executed_at=_CLOSE
        )
    ]
    hits = detect(_window(fills=fills), {})
    close_hits = [h for h in hits if h.pattern_id == PATTERN_MARKING_THE_CLOSE]
    assert len(close_hits) == 1
    assert close_hits[0].reason_code == REASON_DATA_MISSING
    assert close_hits[0].severity == ComplianceVerdict.DENY


def test_spoofing_corrupted_order_qty_fails_closed_not_a_crash():
    orders = [_order(order_id=f"o{i}", qty=_ExplodingDecimal("100")) for i in range(3)]
    fills = [_fill(qty=Decimal("30"))]
    hits = detect(_window(orders=orders, fills=fills), {})
    spoof_hits = [h for h in hits if h.pattern_id == PATTERN_SPOOFING]
    assert len(spoof_hits) == 1
    assert spoof_hits[0].reason_code == REASON_DATA_MISSING
    assert spoof_hits[0].severity == ComplianceVerdict.DENY


# -- Numeric performance (DEEPEN) ------------------------------------------


def test_detect_meets_latency_budget_over_many_calls_on_a_realistic_window():
    """수치 성능 단언: wash-trade는 체결 수의 제곱에 비례하므로(O(n^2)),
    현실적인 윈도 크기(30건)에 대해 다회 반복 호출의 총 지연이 넉넉한
    상한 안에 들어야 한다."""
    fills = [
        _fill(
            fill_id=f"f{i}",
            side="BUY" if i % 2 == 0 else "SELL",
            owner_id=f"owner-{i % 5}",
            executed_at=_T0 + timedelta(seconds=i),
        )
        for i in range(30)
    ]
    orders = [_order(order_id=f"o{i}", qty=Decimal("10")) for i in range(30)]
    window = _window(fills=fills, orders=orders)

    started = time.perf_counter()
    for _ in range(500):
        detect(window, {})
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 2.0, (
        f"500 evaluations of a 30-fill window took {elapsed_s:.3f}s (budget 2.0s)"
    )


# -- Gate-red regression (DEEPEN) -------------------------------------------

_SEVERITY_RANK = {ComplianceVerdict.ALLOW: 0, ComplianceVerdict.WARN: 1, ComplianceVerdict.DENY: 2}


def _worst_severity(hits: list[AbuseHit]) -> ComplianceVerdict:
    """Mirrors the worst-verdict adoption CM-11's `evaluate_tenant_day`
    performs over this leaf's hits (`evaluate_post_trade.py`'s `_record`)."""
    if not hits:
        return ComplianceVerdict.ALLOW
    return max((h.severity for h in hits), key=lambda s: _SEVERITY_RANK[s])


def test_gate_stays_green_for_a_clean_window():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="BUY", tenant_id="tenant-b", executed_at=_T0),
    ]
    hits = detect(_window(fills=fills), {})
    assert _worst_severity(hits) == ComplianceVerdict.ALLOW


def test_gate_stays_amber_not_red_for_a_real_pattern_hit_alone():
    """CM-11's `evaluate_post_trade.py` docstring: the three real CM-10
    detections are WARN, never DENY -- only `DATA_MISSING` escalates to
    DENY/red. A regression here (e.g. someone bumping a real hit's severity
    to DENY) would silently start blocking tenants straight from this leaf."""
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=1)),
    ]
    hits = detect(_window(fills=fills), {})
    assert len(hits) == 1
    assert _worst_severity(hits) == ComplianceVerdict.WARN


def test_gate_turns_red_only_when_data_is_missing():
    hits = detect({"orders": [], "market_close_at": _CLOSE}, {})
    assert _worst_severity(hits) == ComplianceVerdict.DENY


# -- D3 adversarial + multi-instance/replay proof (DEEPEN) ------------------


def test_abuse_hit_rejects_post_construction_tampering():
    """D3 적대적: 컴플라이언스 판정을 좌우하는 `AbuseHit`을 만든 뒤 메모리
    에서 `severity`를 DENY -> WARN으로 바꿔치기하는 시도(다운스트림 코드의
    버그 또는 공격)는 예외 없이 조용히 성공해서는 안 된다. `AbuseHit`은
    이미 `frozen=True` dataclass이므로, 그 방어선이 실제로 걸려 있음을
    실증한다."""
    hit = _missing_hit_for_test()
    with pytest.raises(dataclasses.FrozenInstanceError):
        hit.severity = ComplianceVerdict.WARN  # type: ignore[misc]


def _missing_hit_for_test() -> AbuseHit:
    hits = detect({"orders": [], "market_close_at": _CLOSE}, {})
    return hits[0]


def _replay_in_subprocess(
    window: dict[str, object], params: dict[str, object]
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    hits = detect(window, params)
    return tuple(
        (
            h.pattern_id,
            h.severity.value,
            h.reason_code,
            h.message,
            str(sorted(h.evidence.items(), key=str)),
        )
        for h in hits
    )


def test_detect_replay_across_independent_processes_is_byte_identical():
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 window/params를 각자 평가해도 완전히 동일한
    결과를 내야 한다."""
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)),
    ]
    window = _window(fills=fills)

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [window] * 3, [{}] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
