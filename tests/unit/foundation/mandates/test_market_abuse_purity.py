"""CM-10 `detect()` purity/determinism gates.  Split from test_market_abuse.py
(purity responsibility); D2 evidence: negatives, failure injection, perf.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from src.foundation.mandates.domain.market_abuse import (
    PATTERN_WASH_TRADE,
    REASON_DATA_MISSING,
    detect,
)
from tests.unit.foundation.mandates.market_abuse_fixtures import (
    _T0,
    _fill,
    _window,
)


def test_detect_is_pure_and_deterministic():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)),
    ]
    window = _window(fills=fills)
    assert detect(window, {}) == detect(window, {})


def test_detect_does_not_mutate_its_input_window():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)),
    ]
    window = _window(fills=fills)
    before = list(window["fills"])
    detect(window, {})
    assert list(window["fills"]) == before


def test_detect_empty_window_fails_closed_with_data_missing():
    hits = detect({}, {})
    assert hits
    assert all(h.reason_code == REASON_DATA_MISSING for h in hits)


def test_detect_wash_trade_far_apart_fills_are_not_a_hit():
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(hours=1)),
    ]
    hits = detect(_window(fills=fills), {})
    assert [h for h in hits if h.pattern_id == PATTERN_WASH_TRADE] == []


@pytest.mark.perf
def test_detect_repeated_calls_perf_budget(perf_budget):
    fills = [
        _fill(
            fill_id=f"f{i}",
            side="BUY" if i % 2 else "SELL",
            qty=Decimal("1"),
            executed_at=_T0 + timedelta(seconds=i),
        )
        for i in range(100)
    ]
    window = _window(fills=fills)
    perf_budget.assert_within(lambda: detect(window, {}), budget_ms=500, label="detect(100 fills)")
