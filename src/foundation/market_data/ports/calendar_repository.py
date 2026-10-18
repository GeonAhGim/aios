"""LA-9 — venue trading calendar repository port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

Only domain/application knows this Protocol; the actual implementation
(adapters/postgres_calendar_repository.py, LA-12) does not (§4, item 71).
`VenueCalendar` is a pure domain type already defined in LA-3
(`domain/calendar/session_rules.py`) — reused as-is, not a new DTO.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar


@runtime_checkable
class CalendarRepository(Protocol):
    async def load(self, conn: asyncpg.Connection, venue: Venue, year: int) -> VenueCalendar:
        """`VenueCalendar` reflecting holidays and early closures for the given year.
        If no days are loaded the implementation raises `CalendarNotLoadedError` —
        do not render gap judgments without holiday data (§4.1 fail-closed)."""
        ...

    async def upsert_days(
        self, conn: asyncpg.Connection, venue: Venue, days: list[CalendarDay]
    ) -> None:
        """Year-level load (called by `sync_calendar`). Overwrites on duplicate
        `trade_date` (calendar correction)."""
        ...
