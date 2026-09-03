"""PostgresCalendarRepository + yaml_calendar_source 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-12.
DoD(task-451): "yaml 캘린더가 md_venue_calendar_day로 멱등 적재(같은 파일
2회 적재 시 행 수 불변)", negative 최소 1개 — `ports/calendar_repository.py`
계약대로, 적재되지 않은 venue/year 조회는 `None`이 아니라
`CalendarNotLoadedError`를 던진다(§4.1 fail-closed, 포트 파일은 이
리프에서 수정하지 않는다 — decision, task-451).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.foundation.market_data.adapters.postgres_calendar_repository import (
    CalendarNotLoadedError,
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.yaml_calendar_source import load_calendar
from src.foundation.market_data.contracts.v1 import CalendarDay, Venue

_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
_KRX_2026_YAML = _CONFIG_DIR / "market_calendars" / "KRX_2026.yaml"


@pytest.fixture
def repo(pool):
    return PostgresCalendarRepository(pool)


async def test_load_raises_when_no_days_loaded(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(CalendarNotLoadedError):
            await repo.load(conn, Venue.KIS_US, 1999)


async def test_upsert_then_load_builds_calendar_with_holiday_and_early_close(pool, repo):
    year = 2101  # 실제 데이터와 겹치지 않는 먼 미래 테스트 전용 연도
    holiday = date(year, 1, 1)
    early_close_day = date(year, 6, 15)
    open_at = datetime(year, 6, 15, 13, 30, tzinfo=timezone.utc)
    close_at = datetime(year, 6, 15, 18, 0, tzinfo=timezone.utc)
    days = [
        CalendarDay(
            venue=Venue.KIS_US, trade_date=holiday, is_trading_day=False,
            open_at=None, close_at=None, early_close=False, source="TEST",
        ),
        CalendarDay(
            venue=Venue.KIS_US, trade_date=early_close_day, is_trading_day=True,
            open_at=open_at, close_at=close_at, early_close=True, source="TEST",
        ),
    ]

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_days(conn, Venue.KIS_US, days)

    async with pool.acquire() as conn, conn.transaction():
        calendar = await repo.load(conn, Venue.KIS_US, year)

    assert holiday in calendar.holidays
    assert early_close_day in calendar.early_closes


async def test_upsert_days_is_idempotent_row_count_unchanged(pool, repo):
    year = 2102
    days = [
        CalendarDay(
            venue=Venue.KIS_KRX, trade_date=date(year, 3, 1), is_trading_day=False,
            open_at=None, close_at=None, early_close=False, source="TEST",
        ),
        CalendarDay(
            venue=Venue.KIS_KRX, trade_date=date(year, 5, 5), is_trading_day=False,
            open_at=None, close_at=None, early_close=False, source="TEST",
        ),
    ]

    async def _count(conn) -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM md_venue_calendar_day WHERE venue = $1 "
            "AND trade_date >= $2 AND trade_date <= $3",
            Venue.KIS_KRX.value,
            date(year, 1, 1),
            date(year, 12, 31),
        )

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_days(conn, Venue.KIS_KRX, days)
    async with pool.acquire() as conn:
        first_count = await _count(conn)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_days(conn, Venue.KIS_KRX, days)
    async with pool.acquire() as conn:
        second_count = await _count(conn)

    assert first_count == 2
    assert second_count == first_count


async def test_upsert_days_rejects_mismatched_venue(pool, repo):
    day = CalendarDay(
        venue=Venue.KIS_US, trade_date=date(2103, 1, 1), is_trading_day=False,
        open_at=None, close_at=None, early_close=False, source="TEST",
    )
    with pytest.raises(ValueError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_days(conn, Venue.KIS_KRX, [day])


def test_load_calendar_parses_unverified_krx_yaml():
    days = load_calendar(_KRX_2026_YAML)
    assert days, "KRX_2026.yaml에 최소 1개 휴장일이 있어야 한다"
    assert all(day.venue == Venue.KIS_KRX for day in days)
    assert all(day.source == "UNVERIFIED" for day in days)


async def test_yaml_calendar_loaded_twice_keeps_row_count_stable(pool, repo):
    """DoD 핵심: 같은 yaml 파일을 2회 적재해도 md_venue_calendar_day 행 수가
    바뀌지 않는다(멱등 적재)."""
    days = load_calendar(_KRX_2026_YAML)

    async def _count(conn) -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM md_venue_calendar_day WHERE venue = $1 AND source = $2",
            Venue.KIS_KRX.value,
            "UNVERIFIED",
        )

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_days(conn, Venue.KIS_KRX, days)
    async with pool.acquire() as conn:
        first_count = await _count(conn)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_days(conn, Venue.KIS_KRX, days)
    async with pool.acquire() as conn:
        second_count = await _count(conn)

    assert first_count == len(days)
    assert second_count == first_count
