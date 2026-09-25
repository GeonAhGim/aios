"""PostgresCalendarRepository + yaml_calendar_source 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-12.
DoD(task-451): "yaml 캘린더가 md_venue_calendar_day로 멱등 적재(같은 파일
2회 적재 시 행 수 불변)", negative 최소 1개 — `ports/calendar_repository.py`
계약대로, 적재되지 않은 venue/year 조회는 `None`이 아니라
`CalendarNotLoadedError`를 던진다(§4.1 fail-closed, 포트 파일은 이
리프에서 수정하지 않는다 — decision, task-451).

task-1768: `KRX_2026.yaml`의 `source`는 더 이상 `UNVERIFIED` placeholder가
아니라 KRX 공식 출처 URL + 수집 시각이다(ADR-2026-09-06-H D3). 아래
멱등성 테스트는 그 실제 값을 그대로 재사용해 문자열을 이중 관리하지
않는다.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path

import asyncpg
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
            venue=Venue.KIS_US,
            trade_date=holiday,
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
        ),
        CalendarDay(
            venue=Venue.KIS_US,
            trade_date=early_close_day,
            is_trading_day=True,
            open_at=open_at,
            close_at=close_at,
            early_close=True,
            source="TEST",
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
            venue=Venue.KIS_KRX,
            trade_date=date(year, 3, 1),
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
        ),
        CalendarDay(
            venue=Venue.KIS_KRX,
            trade_date=date(year, 5, 5),
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
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
        venue=Venue.KIS_US,
        trade_date=date(2103, 1, 1),
        is_trading_day=False,
        open_at=None,
        close_at=None,
        early_close=False,
        source="TEST",
    )
    with pytest.raises(ValueError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_days(conn, Venue.KIS_KRX, [day])


def test_load_calendar_parses_krx_yaml_with_verified_source():
    days = load_calendar(_KRX_2026_YAML)
    assert days, "KRX_2026.yaml에 최소 1개 휴장일이 있어야 한다"
    assert all(day.venue == Venue.KIS_KRX for day in days)
    assert all(day.source != "UNVERIFIED" for day in days)
    assert all("collected_at=" in day.source for day in days)


async def test_yaml_calendar_loaded_twice_keeps_row_count_stable(pool, repo):
    """DoD 핵심: 같은 yaml 파일을 2회 적재해도 md_venue_calendar_day 행 수가
    바뀌지 않는다(멱등 적재)."""
    days = load_calendar(_KRX_2026_YAML)
    source = days[0].source

    async def _count(conn) -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM md_venue_calendar_day WHERE venue = $1 AND source = $2",
            Venue.KIS_KRX.value,
            source,
        )

    try:
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
    finally:
        # `source`("collected_at=" 실제 타임스탬프 포함)가 50자를 넘길 수 있어
        # 정리하지 않으면 test_db_transition_trigger.py의 전체이력 downgrade가
        # md_venue_calendar_day.source를 VARCHAR(50)으로 되돌릴 때 이 행이
        # StringDataRightTruncationError로 공유 TEST_DATABASE_URL 세션을 깨뜨린다
        # (test_fa0c_account_scope.py의 `_cleanup_portfolio_test_accounts`와 동일 위생).
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_venue_calendar_day WHERE venue = $1 AND source = $2",
                Venue.KIS_KRX.value,
                source,
            )


# ---- DEEPEN(task-2967, docs/audit/DEPTH_LA_LB_LC.md#451) -----------------
# 실패 주입(monkeypatch) / 수치 성능 단언 / 게이트 적색 재현.
# (postgres_calendar_repository.py의 audit-event 결합 흐름은 이미
# test_calendar_sync_deepen_2901.py에서 sync_calendar 경유로 다뤘다 — 여기서는
# 그 audit 결합 없이 repo 메서드 자체를 직접 겨냥한다.)


async def test_load_connection_failure_propagates_without_fabricating_calendar(
    pool, repo, monkeypatch
):
    """실패 주입: load()의 md_venue_calendar_day 조회 중 커넥션이 끊기면
    (네트워크 단절 흉내) §4.1 fail-closed대로 예외를 그대로 전파해야 한다 —
    `CalendarNotLoadedError`로 뭉개거나 빈 캘린더를 추측해 조립하면 안 된다."""

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object) -> None:
        raise asyncpg.PostgresConnectionError("injected connection failure")

    monkeypatch.setattr(asyncpg.Connection, "fetch", _boom)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.load(conn, Venue.KIS_US, 1999)
    monkeypatch.undo()


@pytest.mark.perf
async def test_load_p95_under_budget(pool, repo):
    """수치 성능 단언: load()는 갭 판정마다 불리는 읽기 경로다(§9.2 LA-12) —
    반복 조회의 p95 지연이 원장 append p95 예산(task-489/LB-18, 30ms)과
    같은 자릿수 안에 있음을 증명한다."""
    year = 2196
    days = [
        CalendarDay(
            venue=Venue.KIS_KRX,
            trade_date=date(year, month, 1),
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
        )
        for month in range(1, 13)
    ]
    try:
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_days(conn, Venue.KIS_KRX, days)

        n = 200
        budget_p95_sec = 0.03
        latencies: list[float] = []
        for _ in range(n):
            start = time.perf_counter()
            async with pool.acquire() as conn, conn.transaction():
                calendar = await repo.load(conn, Venue.KIS_KRX, year)
            latencies.append(time.perf_counter() - start)
            assert len(calendar.holidays) == 12

        latencies.sort()
        p95 = latencies[int(n * 0.95)]
        print(f"[LA-12 load] n={n} p95={p95 * 1000:.2f}ms (budget<{budget_p95_sec * 1000:.0f}ms)")
        assert p95 < budget_p95_sec, f"load() p95가 예산을 넘었습니다: {p95:.4f}s"
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_venue_calendar_day WHERE venue = $1 "
                "AND trade_date >= $2 AND trade_date <= $3",
                Venue.KIS_KRX.value,
                date(year, 1, 1),
                date(year, 12, 31),
            )


async def test_gate_red_when_venue_guard_removed_negative_test_would_fail(pool, repo, monkeypatch):
    """게이트 적색 재현: upsert_days()의 venue 일치 가드(ValueError)를
    제거하는 회귀를 주입하면 venue가 다른 `CalendarDay`도 조용히 적재된다 —
    test_upsert_days_rejects_mismatched_venue가 지키는
    `pytest.raises(ValueError)` 블록이 그 회귀 아래에서 green에서 red로
    뒤집힘을 이 자리에서 직접 재현한다."""
    year = 2197
    day = CalendarDay(
        venue=Venue.KIS_US,
        trade_date=date(year, 1, 1),
        is_trading_day=False,
        open_at=None,
        close_at=None,
        early_close=False,
        source="TEST",
    )

    async def _regressed_upsert_days(
        self: PostgresCalendarRepository, conn: asyncpg.Connection, venue: Venue, days: list
    ) -> None:
        # 회귀: venue 일치 검사(ValueError) 없이 그대로 적재한다.
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
                    d.trade_date,
                    d.is_trading_day,
                    d.open_at,
                    d.close_at,
                    d.early_close,
                    d.source,
                )
                for d in days
            ],
        )

    monkeypatch.setattr(PostgresCalendarRepository, "upsert_days", _regressed_upsert_days)

    try:
        with pytest.raises(pytest.fail.Exception):
            with pytest.raises(ValueError):
                async with pool.acquire() as conn, conn.transaction():
                    await repo.upsert_days(conn, Venue.KIS_KRX, [day])
    finally:
        # 회귀 경로는 원본과 동일하게 호출 인자(venue=KIS_KRX)로 행을 적재한다
        # — 가드가 없으면 KIS_US용 CalendarDay 내용이 KIS_KRX 캘린더에
        # 조용히 섞여 들어간다(venue 오염). 다음 테스트로 새지 않게 지운다.
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_venue_calendar_day WHERE venue = $1 AND trade_date = $2",
                Venue.KIS_KRX.value,
                date(year, 1, 1),
            )
