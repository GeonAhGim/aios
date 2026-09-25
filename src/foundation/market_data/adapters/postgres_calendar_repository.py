"""LA-12 — asyncpg implementation of `CalendarRepository` (ports/calendar_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-12.

`md_venue_calendar_day`(LA-10) does not store every calendar day — it loads only
exceptions: holidays (`is_trading_day=false`) and early close days (`early_close=true`).
Whether a regular session opens is computed by `VenueCalendar.sessions_for`(LA-3) from
the day-of-week + exception combination. `load()` queries only that exception set to
assemble the `VenueCalendar`; regular session specs (tz/open time/day-of-week) are
reused directly from `known_venues.KNOWN_SESSIONS`(LA-3) (no duplicate value definitions).
"""
from __future__ import annotations

from datetime import date, time

import asyncpg

from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

__all__ = ["CalendarNotLoadedError", "PostgresCalendarRepository"]


class CalendarNotLoadedError(Exception):
    """No calendar rows loaded for the target venue/year — §4.1 fail-closed:
    do not make gap determinations without holiday data."""


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
