"""CM-10 failure injection, latency budget and verdict regression tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.market_abuse import (
    PATTERN_MARKING_THE_CLOSE,
    PATTERN_SPOOFING,
    PATTERN_WASH_TRADE,
    REASON_DATA_MISSING,
    AbuseHit,
    detect,
)
from tests.unit.foundation.mandates.market_abuse_fixtures import (
    _CLOSE,
    _T0,
    _fill,
    _order,
    _window,
)

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
            executed_at=_ExplodingDatetime(2026, 1, 5, 5, 0, 0, tzinfo=UTC),
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


@pytest.mark.perf
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
