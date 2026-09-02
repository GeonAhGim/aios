"""LA-5 — market_data/domain/quality/gap_detector.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §8.1, §9.2 LA-5.

핵심 케이스(§8.1): KRX 점심 없음·장 마감 후 결측은 갭 아님, 세션 중 결측
2개 → GAP 2, 크립토 24×7.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import (
    UnknownTimeframeError,
    duration,
    expected_opens,
)


def _calendar(venue: Venue) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _candle(key: SeriesKey, open_time: datetime, tf: Timeframe) -> CandleRecord:
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


def test_krx_no_lunch_break_and_after_close_missing_is_not_gap() -> None:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))  # 금요일, 정규 거래일
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    assert len(expected) >= 3  # 점심시간 결측이 껴 있었다면 연속 구간이 이보다 짧게 끊긴다
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected]
    issues = detect_gaps(candles, Timeframe.H1, sessions)
    assert issues == []  # 장 마감 이후(세션 밖)는 기대 집합에 없으므로 갭으로 잡히지 않는다


def test_krx_two_missing_candles_in_session_yield_two_gap_issues() -> None:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    assert len(expected) >= 3
    missing = {expected[1], expected[2]}
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected if ot not in missing]
    issues = detect_gaps(candles, Timeframe.H1, sessions)
    assert len(issues) == 2
    assert {i.open_time for i in issues} == missing
    assert all(i.type is QualityIssueType.GAP and i.severity is Severity.WARN for i in issues)


def test_crypto_24x7_missing_candle_is_gap() -> None:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M30)
    sessions = _calendar(Venue.BITGET).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.M30, sessions)
    missing_ot = expected[5]
    candles = [_candle(key, ot, Timeframe.M30) for ot in expected if ot != missing_ot]
    issues = detect_gaps(candles, Timeframe.M30, sessions)
    assert len(issues) == 1
    assert issues[0].open_time == missing_ot
    assert issues[0].type is QualityIssueType.GAP


def test_detect_gaps_empty_sessions_returns_empty() -> None:
    assert detect_gaps([], Timeframe.M1, []) == []


def test_detect_gaps_unknown_timeframe_raises() -> None:
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    with pytest.raises(UnknownTimeframeError):
        detect_gaps([], cast(Timeframe, "2h"), sessions)
