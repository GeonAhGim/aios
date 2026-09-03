"""DC-6 — domain/coverage/registry 단위 테스트(병합·질의).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-6, §4.1, §9.2 DC-6.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.domain.coverage.registry import coverage_for, merge_spans

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_OTHER_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def _span(
    *,
    instrument_id: str = _VALID_ULID,
    venue: Venue = Venue.BITGET,
    asset_class: AssetClass = AssetClass.CRYPTO,
    timeframe: Timeframe = Timeframe.D1,
    quality_grade: QualityGrade = QualityGrade.RAW,
    start_at: datetime,
    end_at: datetime,
) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=instrument_id,
        venue=venue,
        asset_class=asset_class,
        timeframe=timeframe,
        quality_grade=quality_grade,
        start_at=start_at,
        end_at=end_at,
    )


def _instrument(instrument_id: str = _VALID_ULID) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=_dt(1),
    )


# ---- CoverageSpan 계약 자체(negative) ----


def test_coverage_span_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _span(start_at=_dt(5), end_at=_dt(1))


def test_coverage_span_rejects_zero_length() -> None:
    with pytest.raises(ValidationError):
        _span(start_at=_dt(1), end_at=_dt(1))


# ---- merge_spans ----


def test_merge_overlapping_spans_combined_into_one() -> None:
    a = _span(start_at=_dt(1), end_at=_dt(5))
    b = _span(start_at=_dt(3), end_at=_dt(8))
    result = merge_spans([a, b])
    assert result == [_span(start_at=_dt(1), end_at=_dt(8))]


def test_merge_adjacent_touching_spans_combined_into_one() -> None:
    """경계가 정확히 맞닿은(a.end_at == b.start_at) span은 연속 구간이므로 병합."""
    a = _span(start_at=_dt(1), end_at=_dt(5))
    b = _span(start_at=_dt(5), end_at=_dt(9))
    result = merge_spans([a, b])
    assert result == [_span(start_at=_dt(1), end_at=_dt(9))]


def test_merge_discontinuous_spans_stay_separate() -> None:
    """사이에 간격이 있으면(불연속) 별개 span으로 남는다."""
    a = _span(start_at=_dt(1), end_at=_dt(3))
    b = _span(start_at=_dt(5), end_at=_dt(9))
    result = merge_spans([a, b])
    assert result == [a, b]


def test_merge_fully_contained_span_absorbed_without_shrinking() -> None:
    outer = _span(start_at=_dt(1), end_at=_dt(10))
    inner = _span(start_at=_dt(3), end_at=_dt(5))
    result = merge_spans([inner, outer])
    assert result == [outer]


def test_merge_is_order_independent() -> None:
    a = _span(start_at=_dt(1), end_at=_dt(5))
    b = _span(start_at=_dt(3), end_at=_dt(8))
    c = _span(start_at=_dt(20), end_at=_dt(22))
    assert merge_spans([a, b, c]) == merge_spans([c, b, a]) == merge_spans([b, c, a])


def test_merge_no_overlap_remains_in_result() -> None:
    """§4.1 EXCLUDE 제약과 동일 의미론 — 병합 결과 안에 겹침이 남으면 안 된다."""
    spans = [
        _span(start_at=_dt(1), end_at=_dt(4)),
        _span(start_at=_dt(2), end_at=_dt(6)),
        _span(start_at=_dt(6), end_at=_dt(9)),
        _span(start_at=_dt(15), end_at=_dt(18)),
        _span(start_at=_dt(16), end_at=_dt(17)),
    ]
    result = merge_spans(spans)
    ordered = sorted(result, key=lambda s: s.start_at)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end_at < nxt.start_at


def test_merge_different_venue_not_merged_even_if_period_overlaps() -> None:
    a = _span(venue=Venue.BITGET, start_at=_dt(1), end_at=_dt(5))
    b = _span(venue=Venue.KIS_KRX, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([a, b])
    assert len(result) == 2
    assert set(result) == {a, b}


def test_merge_different_quality_grade_not_merged_even_if_period_overlaps() -> None:
    a = _span(quality_grade=QualityGrade.RAW, start_at=_dt(1), end_at=_dt(5))
    b = _span(quality_grade=QualityGrade.GOLD, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([a, b])
    assert len(result) == 2
    assert set(result) == {a, b}


def test_merge_different_instrument_not_merged_even_if_period_overlaps() -> None:
    a = _span(instrument_id=_VALID_ULID, start_at=_dt(1), end_at=_dt(5))
    b = _span(instrument_id=_OTHER_ULID, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([a, b])
    assert len(result) == 2
    assert set(result) == {a, b}


def test_merge_empty_input_returns_empty() -> None:
    assert merge_spans([]) == []


# ---- coverage_for ----


def test_coverage_for_filters_by_instrument_and_timeframe_then_merges() -> None:
    instrument = _instrument()
    matching_a = _span(timeframe=Timeframe.D1, start_at=_dt(1), end_at=_dt(5))
    matching_b = _span(timeframe=Timeframe.D1, start_at=_dt(5), end_at=_dt(9))
    other_tf = _span(timeframe=Timeframe.H1, start_at=_dt(1), end_at=_dt(5))
    other_instrument = _span(
        instrument_id=_OTHER_ULID, timeframe=Timeframe.D1, start_at=_dt(1), end_at=_dt(5)
    )
    result = coverage_for(
        [matching_a, matching_b, other_tf, other_instrument], instrument, Timeframe.D1
    )
    assert result == [_span(timeframe=Timeframe.D1, start_at=_dt(1), end_at=_dt(9))]


def test_coverage_for_no_matching_span_returns_empty_list() -> None:
    """커버리지 선언이 전혀 없으면 빈 리스트 — 호출자가 DATA_COVERAGE_MISSING을
    판정할 근거(§4.1 fail-closed, 0/NaN 채움 아님)."""
    instrument = _instrument()
    unrelated = _span(
        instrument_id=_OTHER_ULID, timeframe=Timeframe.D1, start_at=_dt(1), end_at=_dt(5)
    )
    result = coverage_for([unrelated], instrument, Timeframe.D1)
    assert result == []
