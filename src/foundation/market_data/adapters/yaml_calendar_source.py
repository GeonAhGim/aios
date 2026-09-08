"""LA-12 — yaml 파일 → `CalendarDay` 리스트 어댑터.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-12, §10 R4.
ADR-2026-09-06-H D3 (task-1768): exchange calendars are built in-house —
loading requires the primary source (the exchange's official notice /
public data) to be recorded together as two fields, `source` (URL) and
`collected_at` (collection time). Lists that haven't been cross-checked
yet must be marked `source: UNVERIFIED` so that status isn't hidden
(fail-closed) — this loader rejects any file that lacks the `source` /
`collected_at` fields outright. The returned `CalendarDay.source` combines
them as `"<source> | collected_at=<date>"` and carries that straight into
the `md_venue_calendar_day.source` column (LA-10, VARCHAR(500)) — this
keeps the source and collection time together in one place without a
separate column.

정규 개장일은 목록에 없다 — `holidays`(휴장)와 `early_closes`(조기폐장)만
예외로 적는다. 정규 개장 여부는 `known_venues.KNOWN_SESSIONS`(LA-3)의
요일 규칙으로 계산되므로 여기서 다시 나열할 필요가 없다.
"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS

__all__ = ["CalendarSourceError", "load_calendar"]


class CalendarSourceError(ValueError):
    """A yaml schema error, or a missing `source`/`collected_at`/`venue`
    field (§10 R4: without the UNVERIFIED marking, loading is rejected outright)."""


def load_calendar(path: Path) -> list[CalendarDay]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise CalendarSourceError(f"최상위 구조가 dict가 아님: {path}")

    source = data.get("source")
    if not source:
        raise CalendarSourceError(f"source 필드 없음(§10 R4 UNVERIFIED 표기 필수): {path}")
    collected_at = data.get("collected_at")
    if not collected_at:
        raise CalendarSourceError(f"collected_at 필드 없음(출처·수집 시각 동시 기록 필수): {path}")
    source = f"{source} | collected_at={collected_at}"

    venue = _parse_venue(data.get("venue"))
    tz = KNOWN_SESSIONS[venue.value].tz

    days: dict[date, CalendarDay] = {}
    for raw_holiday in data.get("holidays") or []:
        trade_date = _parse_date(raw_holiday)
        days[trade_date] = CalendarDay(
            venue=venue,
            trade_date=trade_date,
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source=source,
        )

    session = KNOWN_SESSIONS[venue.value]
    for entry in data.get("early_closes") or []:
        trade_date = _parse_date(entry["date"])
        close_time = _parse_time(entry["close_time"])
        days[trade_date] = CalendarDay(
            venue=venue,
            trade_date=trade_date,
            is_trading_day=True,
            open_at=datetime.combine(trade_date, session.open_time, tzinfo=tz),
            close_at=datetime.combine(trade_date, close_time, tzinfo=tz),
            early_close=True,
            source=source,
        )

    return sorted(days.values(), key=lambda d: d.trade_date)


def _parse_venue(raw: Any) -> Venue:
    try:
        return Venue(raw)
    except ValueError as exc:
        raise CalendarSourceError(f"알 수 없는 venue: {raw!r}") from exc


def _parse_date(raw: Any) -> date:
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def _parse_time(raw: Any) -> time:
    return time.fromisoformat(str(raw))
