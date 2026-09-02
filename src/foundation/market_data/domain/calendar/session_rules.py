"""LA-3 — venue별 세션 창 계산(정규장·조기폐장·24×7).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-3.
휴장일·조기폐장 목록은 `VenueCalendar` 생성자 인자로만 받는다(순수 도메인 —
DB·yaml 읽기는 `adapters/postgres_calendar_repository.py`·
`adapters/yaml_calendar_source.py`(LA-12) 소관). 타임존은 표준 `zoneinfo`만
쓴다(107번: 새 의존성 추가 금지).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from src.foundation.market_data.contracts.v1 import SessionWindow

_MAX_LOOKAHEAD_DAYS = 30


class CalendarExhaustedError(Exception):
    """`next_open`이 조회 상한 내에서 거래일을 찾지 못했다(연속 휴장 이상 신호)."""


@dataclass(frozen=True)
class SessionSpec:
    """venue 로컬 시각 기준 정규장 스펙."""

    tz: ZoneInfo
    open_time: time
    close_time: time
    weekdays: frozenset[int]  # date.weekday(): Mon=0..Sun=6
    continuous: bool = False


@dataclass(frozen=True)
class VenueCalendar:
    venue: str
    tz: ZoneInfo
    regular: SessionSpec
    holidays: frozenset[date] = frozenset()
    early_closes: dict[date, time] = field(default_factory=dict)

    def sessions_for(self, day: date) -> list[SessionWindow]:
        if self.regular.continuous:
            open_at = datetime.combine(day, time.min, tzinfo=self.tz)
            close_at = open_at + timedelta(days=1)
            return [SessionWindow(open_at=open_at, close_at=close_at, kind="CONTINUOUS")]
        if day in self.holidays or day.weekday() not in self.regular.weekdays:
            return []
        close_time = self.early_closes.get(day, self.regular.close_time)
        kind: Literal["REGULAR", "EARLY_CLOSE"]
        kind = "EARLY_CLOSE" if day in self.early_closes else "REGULAR"
        open_at = datetime.combine(day, self.regular.open_time, tzinfo=self.tz)
        close_at = datetime.combine(day, close_time, tzinfo=self.tz)
        return [SessionWindow(open_at=open_at, close_at=close_at, kind=kind)]

    def is_open(self, at: datetime) -> bool:
        if self.regular.continuous:
            return True
        for window in self.sessions_for(self.trading_day_of(at)):
            if window.open_at <= at < window.close_at:
                return True
        return False

    def next_open(self, at: datetime) -> datetime:
        if self.regular.continuous:
            return at
        start_day = self.trading_day_of(at)
        for offset in range(_MAX_LOOKAHEAD_DAYS):
            windows = self.sessions_for(start_day + timedelta(days=offset))
            if not windows:
                continue
            window = windows[0]
            if at < window.open_at:
                return window.open_at
            if at < window.close_at:
                return at
        raise CalendarExhaustedError(
            f"{self.venue}: no trading day within {_MAX_LOOKAHEAD_DAYS}d of {at.isoformat()}"
        )

    def trading_day_of(self, at: datetime) -> date:
        return at.astimezone(self.tz).date()
