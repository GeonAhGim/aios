"""LA-9 — venue 거래 캘린더 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application은 이 Protocol만 알고, 실제 구현
(adapters/postgres_calendar_repository.py, LA-12)은 모른다(71번 §4).
`VenueCalendar`는 LA-3(`domain/calendar/session_rules.py`)에서 이미 정의된
순수 도메인 타입을 그대로 재사용한다(신규 DTO 아님).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar


@runtime_checkable
class CalendarRepository(Protocol):
    async def load(self, conn: asyncpg.Connection, venue: Venue, year: int) -> VenueCalendar:
        """해당 연도 휴장일·조기폐장을 반영한 `VenueCalendar`. 적재된 일자가
        없으면 구현체가 `CalendarNotLoadedError`를 던진다 — 휴장일 데이터
        없이 갭 판정을 내리지 않는다(§4.1 fail-closed)."""
        ...

    async def upsert_days(
        self, conn: asyncpg.Connection, venue: Venue, days: list[CalendarDay]
    ) -> None:
        """연도 단위 적재(`sync_calendar` 소관 호출). 같은 `trade_date`
        재적재는 덮어쓴다(캘린더 정정)."""
        ...
