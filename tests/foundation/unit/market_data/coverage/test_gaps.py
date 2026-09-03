"""DC-7 — domain/coverage/gaps 단위 테스트(fail-closed 판정).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-7(선행 DC-6), §9.2 DC-7.

DoD: 커버리지 선언 없음·부분 커버·세션 휴장(갭 아님) 세 판정이 갈리는 것,
판정 불가가 fail-closed(예외)로 떨어지는 것, 갭 목록이 결정론적으로
정렬되는 것을 negative 테스트로 단언한다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from random import Random
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.coverage.gaps import (
    CoverageGap,
    GapReason,
    IndeterminateCoverageError,
    plan_fetch,
)
from src.foundation.market_data.domain.timeframe import duration

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_OTHER_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def _calendar(venue: Venue) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _dt(hour: int, day: int = 4) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _span(
    *,
    instrument_id: str = _ULID,
    venue: Venue = Venue.BITGET,
    timeframe: Timeframe = Timeframe.H1,
    start_at: datetime,
    end_at: datetime,
) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=instrument_id,
        venue=venue,
        asset_class=AssetClass.CRYPTO,
        timeframe=timeframe,
        quality_grade=QualityGrade.RAW,
        start_at=start_at,
        end_at=end_at,
    )


def _candle(open_time: datetime, tf: Timeframe = Timeframe.H1) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=tf)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + duration(tf),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


# ---- 세 갈래 판정 ----


def test_no_coverage_declared_yields_single_not_covered_gap() -> None:
    gaps = plan_fetch(
        spans=[],
        candles=[],
        tf=Timeframe.H1,
        calendar=_calendar(Venue.BITGET),
        range_start=_dt(0),
        range_end=_dt(4),
    )
    assert gaps == [CoverageGap(_dt(0), _dt(4), GapReason.NOT_COVERED)]


def test_partial_coverage_and_missing_candle_yield_distinct_reasons() -> None:
    """[00:00,04:00)만 선언됨. 그 안에서도 02:00 캔들만 결측 —
    NOT_COVERED(04:00~06:00)와 MISSING_CANDLES(02:00~03:00)가 따로 갈린다."""
    span = _span(start_at=_dt(0), end_at=_dt(4))
    candles = [_candle(_dt(0)), _candle(_dt(1)), _candle(_dt(3))]
    gaps = plan_fetch(
        spans=[span],
        candles=candles,
        tf=Timeframe.H1,
        calendar=_calendar(Venue.BITGET),
        range_start=_dt(0),
        range_end=_dt(6),
    )
    assert gaps == [
        CoverageGap(_dt(2), _dt(3), GapReason.MISSING_CANDLES),
        CoverageGap(_dt(4), _dt(6), GapReason.NOT_COVERED),
    ]


def test_fully_covered_with_all_candles_present_yields_no_gap() -> None:
    span = _span(start_at=_dt(0), end_at=_dt(4))
    candles = [_candle(_dt(h)) for h in range(0, 4)]
    gaps = plan_fetch(
        spans=[span],
        candles=candles,
        tf=Timeframe.H1,
        calendar=_calendar(Venue.BITGET),
        range_start=_dt(0),
        range_end=_dt(4),
    )
    assert gaps == []


def test_market_closed_period_is_not_a_gap_even_without_coverage() -> None:
    """토요일(휴장)은 세션이 없으므로, 커버리지 선언이 전혀 없어도 갭이 아니다."""
    saturday = date(2026, 9, 5)
    assert saturday.weekday() == 5
    gaps = plan_fetch(
        spans=[],
        candles=[],
        tf=Timeframe.H1,
        calendar=_calendar(Venue.KIS_KRX),
        range_start=datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc),
        range_end=datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc),
    )
    assert gaps == []


# ---- fail-closed(판정 불가) ----


def test_mismatched_timeframe_span_raises_instead_of_empty_gap_list() -> None:
    span = _span(timeframe=Timeframe.M1, start_at=_dt(0), end_at=_dt(4))
    with pytest.raises(IndeterminateCoverageError):
        plan_fetch(
            spans=[span],
            candles=[],
            tf=Timeframe.H1,
            calendar=_calendar(Venue.BITGET),
            range_start=_dt(0),
            range_end=_dt(4),
        )


def test_mismatched_instrument_id_spans_raise() -> None:
    a = _span(instrument_id=_ULID, start_at=_dt(0), end_at=_dt(2))
    b = _span(instrument_id=_OTHER_ULID, start_at=_dt(2), end_at=_dt(4))
    with pytest.raises(IndeterminateCoverageError):
        plan_fetch(
            spans=[a, b],
            candles=[],
            tf=Timeframe.H1,
            calendar=_calendar(Venue.BITGET),
            range_start=_dt(0),
            range_end=_dt(4),
        )


def test_naive_datetime_range_raises() -> None:
    with pytest.raises(IndeterminateCoverageError):
        plan_fetch(
            spans=[],
            candles=[],
            tf=Timeframe.H1,
            calendar=_calendar(Venue.BITGET),
            range_start=datetime(2026, 9, 4, 0, 0),
            range_end=_dt(4),
        )


def test_reversed_range_raises() -> None:
    with pytest.raises(IndeterminateCoverageError):
        plan_fetch(
            spans=[],
            candles=[],
            tf=Timeframe.H1,
            calendar=_calendar(Venue.BITGET),
            range_start=_dt(4),
            range_end=_dt(0),
        )


# ---- 결정론적 정렬 ----


def test_gap_list_is_sorted_and_order_independent_of_input() -> None:
    spans = [_span(start_at=_dt(0), end_at=_dt(2)), _span(start_at=_dt(4), end_at=_dt(6))]
    candles = [_candle(_dt(0)), _candle(_dt(1))]
    rng = Random(1234)
    baseline = None
    for _ in range(5):
        shuffled_spans = spans[:]
        shuffled_candles = candles[:]
        rng.shuffle(shuffled_spans)
        rng.shuffle(shuffled_candles)
        gaps = plan_fetch(
            spans=shuffled_spans,
            candles=shuffled_candles,
            tf=Timeframe.H1,
            calendar=_calendar(Venue.BITGET),
            range_start=_dt(0),
            range_end=_dt(8),
        )
        assert gaps == sorted(gaps)
        if baseline is None:
            baseline = gaps
        else:
            assert gaps == baseline
    assert baseline == [
        CoverageGap(_dt(2), _dt(4), GapReason.NOT_COVERED),
        CoverageGap(_dt(4), _dt(6), GapReason.MISSING_CANDLES),
        CoverageGap(_dt(6), _dt(8), GapReason.NOT_COVERED),
    ]
