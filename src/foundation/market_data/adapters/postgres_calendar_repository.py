"""LA-12 — `CalendarRepository`(ports/calendar_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-12.

`md_venue_calendar_day`(LA-10)는 거래일을 전부 저장하지 않는다 — 휴장일
(`is_trading_day=false`)과 조기폐장(`early_close=true`)만 예외로 적재하고,
정규 개장 여부는 `VenueCalendar.sessions_for`(LA-3)이 요일+예외 조합으로
계산한다. `load()`는 그 예외 집합만 조회해 `VenueCalendar`를 조립하고,
정규 세션 스펙(tz/개장시각/요일)은 `known_venues.KNOWN_SESSIONS`(LA-3)를
그대로 재사용한다(값 중복 정의 금지).
"""
from __future__ import annotations

from datetime import date, time

import asyncpg

from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

__all__ = ["CalendarNotLoadedError", "PostgresCalendarRepository"]


class CalendarNotLoadedError(Exception):
    """`load()` 대상 venue/year에 적재된 캘린더 행이 없음 — §4.1 fail-closed:
    휴장일 데이터 없이 갭 판정을 내리지 않는다."""


class PostgresCalendarRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def load(self, conn: asyncpg.Connection, venue: Venue, year: int) -> VenueCalendar:
        rows = await conn.fetch(
            "SELECT trade_date, is_trading_day, close_at, early_close "
            "FROM md_venue_calendar_day "
            "WHERE venue = $1 AND trade_date >= $2 AND trade_date <= $3 "
            "ORDER BY trade_date",
            venue.value,
            date(year, 1, 1),
            date(year, 12, 31),
        )
        if not rows:
            raise CalendarNotLoadedError(f"적재된 캘린더 없음: venue={venue.value} year={year}")

        session = KNOWN_SESSIONS[venue.value]
        holidays: set[date] = set()
        early_closes: dict[date, time] = {}
        for row in rows:
            if not row["is_trading_day"]:
                holidays.add(row["trade_date"])
            elif row["early_close"]:
                early_closes[row["trade_date"]] = row["close_at"].astimezone(session.tz).time()

        return VenueCalendar(
            venue=venue.value,
            tz=session.tz,
            regular=session,
            holidays=frozenset(holidays),
            early_closes=early_closes,
        )

    async def upsert_days(
        self, conn: asyncpg.Connection, venue: Venue, days: list[CalendarDay]
    ) -> None:
        for day in days:
            if day.venue is not venue:
                raise ValueError(
                    f"venue 불일치: 호출 인자={venue.value} CalendarDay.venue={day.venue.value}"
                )

        await conn.executemany(
            "INSERT INTO md_venue_calendar_day "
            "(venue, trade_date, is_trading_day, open_at, close_at, early_close, source) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) "
            "ON CONFLICT (venue, trade_date) DO UPDATE SET "
            "is_trading_day = EXCLUDED.is_trading_day, "
            "open_at = EXCLUDED.open_at, "
            "close_at = EXCLUDED.close_at, "
            "early_close = EXCLUDED.early_close, "
            "source = EXCLUDED.source",
            [
                (
                    venue.value,
                    day.trade_date,
                    day.is_trading_day,
                    day.open_at,
                    day.close_at,
                    day.early_close,
                    day.source,
                )
                for day in days
            ],
        )
