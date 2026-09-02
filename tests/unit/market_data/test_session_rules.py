"""LA-3 — session_rules/known_venues 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1, §9.2 LA-3.
DoD 행 전부: KRX 15:30 KST 마감 == 06:30 UTC, DST 전후 US 마감(EST/EDT 각각),
조기폐장일, 휴장일 is_open=False, 크립토 next_open == at.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import (
    CalendarExhaustedError,
    VenueCalendar,
)


def _calendar(
    venue: Venue,
    *,
    holidays: frozenset[date] = frozenset(),
    early_closes: dict[date, time] | None = None,
) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(
        venue=venue.value,
        tz=spec.tz,
        regular=spec,
        holidays=holidays,
        early_closes=early_closes or {},
    )


def test_krx_close_15_30_kst_equals_06_30_utc() -> None:
    cal = _calendar(Venue.KIS_KRX)
    windows = cal.sessions_for(date(2026, 9, 4))  # 금요일, 정규 거래일
    assert len(windows) == 1
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 9, 4, 6, 30, tzinfo=timezone.utc
    )


def test_us_close_est_before_dst() -> None:
    cal = _calendar(Venue.KIS_US)
    windows = cal.sessions_for(date(2026, 1, 6))  # 화요일, 겨울(EST, UTC-5)
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 1, 6, 21, 0, tzinfo=timezone.utc
    )


def test_us_close_edt_after_dst() -> None:
    cal = _calendar(Venue.KIS_US)
    windows = cal.sessions_for(date(2026, 7, 7))  # 화요일, 여름(EDT, UTC-4)
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 7, 7, 20, 0, tzinfo=timezone.utc
    )


def test_early_close_day_shortens_session() -> None:
    day = date(2026, 9, 4)
    cal = _calendar(Venue.KIS_KRX, early_closes={day: time(13, 0)})
    windows = cal.sessions_for(day)
    assert len(windows) == 1
    assert windows[0].kind == "EARLY_CLOSE"
    assert windows[0].close_at == datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_holiday_is_open_false() -> None:
    day = date(2026, 9, 4)
    cal = _calendar(Venue.KIS_KRX, holidays=frozenset({day}))
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.is_open(at) is False
    assert cal.sessions_for(day) == []


def test_regular_trading_day_is_open_during_session() -> None:
    cal = _calendar(Venue.KIS_KRX)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.is_open(at) is True


def test_crypto_next_open_equals_at() -> None:
    cal = _calendar(Venue.BITGET)
    at = datetime(2026, 9, 4, 3, 17, tzinfo=timezone.utc)
    assert cal.next_open(at) == at
    assert cal.is_open(at) is True


def test_next_open_skips_weekend_to_monday() -> None:
    cal = _calendar(Venue.KIS_KRX)
    saturday = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    next_open = cal.next_open(saturday)
    assert next_open == datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_next_open_raises_when_calendar_exhausted() -> None:
    day = date(2026, 9, 4)
    all_holidays = frozenset(day + timedelta(days=i) for i in range(60))
    cal = _calendar(Venue.KIS_KRX, holidays=all_holidays)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    with pytest.raises(CalendarExhaustedError):
        cal.next_open(at)
