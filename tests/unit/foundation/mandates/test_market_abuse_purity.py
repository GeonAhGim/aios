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


# ---------------------------------------------------------------------------
# Negative tests (D2): invalid/corrupted inputs must not produce false negatives
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_detect_fills_none_fails_closed_with_data_missing():
    """CM-10 I-02: fills가 None이면 각 패턴별 DATA_MISSING 히트 3개를 반환한다.
    detect()는 None을 iterable로 취급해 crash하지 않고 fail-closed해야 한다."""
    window = _window(fills=None)
    hits = detect(window, {})
    assert len(hits) == 3
    assert all(h.reason_code == REASON_DATA_MISSING for h in hits)


@pytest.mark.negative
def test_detect_fill_missing_executed_at_returns_data_missing_hit():
    """CM-10 I-02: fill dict에 executed_at이 없으면 ArithmeticError/KeyError가
    _safe()로 포착되어 DATA_MISSING 히트로Surface된다. silent gap이 아니다."""
    fills: list[dict[str, object]] = [_fill(fill_id="f1")]
    # Remove executed_at to simulate corrupted data
    fills[0].pop("executed_at", None)
    hits = detect(_window(fills=fills), {})
    assert any(h.reason_code == REASON_DATA_MISSING for h in hits)


@pytest.mark.negative
def test_detect_different_tenants_no_false_wash_trade():
    """CM-10 Pattern 1 negative: 서로 다른 tenant의 fill은 같은 owner라도
    wash trade로 오인되지 않는다. false positive는 금지."""
    fills = [
        _fill(fill_id="f1", side="BUY", owner_id="owner-1", tenant_id="tenant-a"),
        _fill(fill_id="f2", side="SELL", owner_id="owner-1", tenant_id="tenant-b"),
    ]
    hits = detect(_window(fills=fills), {})
    assert all(h.pattern_id != PATTERN_WASH_TRADE for h in hits)


@pytest.mark.negative
def test_detect_different_instruments_no_false_wash_trade():
    """CM-10 Pattern 1 negative: 서로 다른 instrument의 fill은 wash trade가
    아니다. false positive를 적발한다."""
    fills = [
        _fill(fill_id="f1", side="BUY", instrument_id="005930"),
        _fill(fill_id="f2", side="SELL", instrument_id="105560"),
    ]
    hits = detect(_window(fills=fills), {})
    assert all(h.pattern_id != PATTERN_WASH_TRADE for h in hits)


@pytest.mark.negative
def test_detect_same_side_no_false_wash_trade():
    """CM-10 Pattern 1 negative: 같은 방향(BUY/BUY 또는 SELL/SELL) fill은
    wash trade 패턴에 매칭되지 않는다."""
    fills = [
        _fill(fill_id="f1", side="BUY"),
        _fill(fill_id="f2", side="BUY"),
    ]
    hits = detect(_window(fills=fills), {})
    assert all(h.pattern_id != PATTERN_WASH_TRADE for h in hits)


@pytest.mark.negative
def test_detect_empty_fills_list_returns_no_hits():
    """CM-10: fills가 빈 리스트이면 DATA_MISSING이 아니라 빈 결과([])를
    반환한다. 빈 리스트는 '데이터 없음'이 아니라 '검찰 대상 없음'이다."""
    hits = detect(_window(fills=[]), {})
    assert hits == []
