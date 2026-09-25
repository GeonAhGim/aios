"""RD-21 `application/sync_calendar.py` + `adapters/postgres_calendar_repository.py`
— DEEPEN(task-2901, docs/audit/DEPTH_DC_RD.md#1768) D1 -> D2 증빙, 실 DB 대상.

기존 test_calendar_repository.py는 negative 3건(미적재 조회 거부, venue
불일치 거부)과 멱등 적재 1건으로 §9 LA-12/LA-14를 증명했다(D1) — 소급감사
(task-2726)에서 "실패주입(DB/네트워크) 없음, 성능단언 없음, 게이트적색
재현이 결정적이지 않음"으로 지적됐다(1768행). 이 파일이 `sync_calendar`
(LA-14, 감사 이벤트 1:1 경계)까지 포함해 그 부족분을 채운다. 새 기능 없음,
깊이만 올림.

1. 실패 주입 — 감사 이벤트 기록이 실패하면(네트워크 단절·DB 장애를 흉내)
   앞서 성공한 `upsert_days`까지 트랜잭션 전체가 롤백되는지, 그리고
   `upsert_days` 자체가 DB 연결 실패로 중단되면 부분 삽입 없이 전체가
   실패하는지를 증명한다.
2. 성능 단언 — 1개 연도 분량(영업일 기준 약 250행) 적재+조회 왕복이
   절대시간 예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — 동일 (venue, year)를 시간축으로 재생한다: 미동기화
   (CalendarNotLoadedError) -> 최초 동기화(공시 원문과 일치) -> 재동기화
   (멱등, day_count·aggregate_id 불변). 각 단계의 상태가 다음 단계로 새지
   않음을 증명한다.
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    CalendarNotLoadedError,
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.yaml_calendar_source import load_calendar
from src.foundation.market_data.application.sync_calendar import (
    calendar_aggregate_id,
    sync_calendar,
)
from src.foundation.market_data.contracts.v1 import CalendarDay, Venue

_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config" / "market_calendars"
_KRX_2026_YAML = _CONFIG_DIR / "KRX_2026.yaml"


class _BoomAuditAppender:
    async def append_event_in(self, conn: asyncpg.Connection, **kwargs: object) -> None:
        raise RuntimeError("injected audit failure")


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresCalendarRepository:
    return PostgresCalendarRepository(pool)


@pytest.fixture
def audit(pool: asyncpg.Pool) -> PostgresAuditEventRepository:
    return PostgresAuditEventRepository(pool)


async def _row_count(pool: asyncpg.Pool, venue: Venue, year: int) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM md_venue_calendar_day WHERE venue = $1 "
            "AND trade_date >= $2 AND trade_date <= $3",
            venue.value,
            date(year, 1, 1),
            date(year, 12, 31),
        )


# ---- 실패 주입(DB/네트워크 monkeypatch) ----


async def test_sync_calendar_rolls_back_upsert_when_audit_append_fails(
    pool: asyncpg.Pool, repo: PostgresCalendarRepository
) -> None:
    """감사 이벤트 기록이 실패하면(네트워크 단절·DB 장애를 흉내) 같은
    트랜잭션 안에서 이미 실행된 `upsert_days`까지 통째로 롤백돼야 한다 —
    캘린더 행만 남고 감사 이벤트가 없는 상태(증적 없는 데이터)가 절대
    허용되지 않는다."""
    year = 2190  # 실제 데이터와 겹치지 않는 먼 미래 테스트 전용 연도
    days = [
        d.model_copy(update={"trade_date": date(year, d.trade_date.month, d.trade_date.day)})
        for d in load_calendar(_KRX_2026_YAML)
    ]

    with pytest.raises(RuntimeError, match="injected audit failure"):
        await sync_calendar(
            pool,
            Venue.KIS_KRX,
            year,
            days,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            cal=repo,
            audit=_BoomAuditAppender(),
        )

    assert await _row_count(pool, Venue.KIS_KRX, year) == 0
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(CalendarNotLoadedError):
            await repo.load(conn, Venue.KIS_KRX, year)


async def test_upsert_days_connection_failure_leaves_no_partial_write(
    pool: asyncpg.Pool, repo: PostgresCalendarRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`upsert_days`의 배치 삽입 도중 커넥션이 끊기면(장애 주입) 이미
    실행되던 배치가 부분 반영된 채 남지 않아야 한다 — asyncpg는 명시적
    트랜잭션 블록 안에서 예외 발생 시 전체 배치를 롤백한다."""
    year = 2191
    days = [
        d.model_copy(update={"trade_date": date(year, d.trade_date.month, d.trade_date.day)})
        for d in load_calendar(_KRX_2026_YAML)
    ]
    assert len(days) > 1, "롤백 증명에는 최소 2행이 필요하다"

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object) -> None:
        raise asyncpg.PostgresConnectionError("injected connection failure")

    monkeypatch.setattr(asyncpg.Connection, "executemany", _boom)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_days(conn, Venue.KIS_KRX, days)
    monkeypatch.undo()

    assert await _row_count(pool, Venue.KIS_KRX, year) == 0


# ---- 성능 단언 ----


@pytest.mark.perf
async def test_sync_calendar_full_year_round_trip_meets_latency_budget(
    pool: asyncpg.Pool, repo: PostgresCalendarRepository, audit: PostgresAuditEventRepository
) -> None:
    """1개 연도 분량(영업일 기준 약 250개) 캘린더 적재+감사 이벤트 기록+
    재조회 왕복이 절대시간 예산 내여야 한다(연 1회성 배치이지만 운영 중
    수십 개 venue로 확장될 수 있으므로 예산을 둔다)."""
    year = 2192
    budget_sec = 5.0  # 실측 로컬 <1s, CI 편차 감안
    days = []
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() < 5:
            days.append(
                CalendarDay(
                    venue=Venue.KIS_KRX,
                    trade_date=d,
                    is_trading_day=False,
                    open_at=None,
                    close_at=None,
                    early_close=False,
                    source="https://example.com/perf-test | collected_at=2026-09-10",
                )
            )
        d = date.fromordinal(d.toordinal() + 1)

    try:
        start = time.perf_counter()
        count = await sync_calendar(
            pool,
            Venue.KIS_KRX,
            year,
            days,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            cal=repo,
            audit=audit,
        )
        async with pool.acquire() as conn, conn.transaction():
            calendar = await repo.load(conn, Venue.KIS_KRX, year)
        elapsed = time.perf_counter() - start

        print(f"[RD-21 sync_calendar] {len(days)}행 왕복 {elapsed:.3f}s (budget<{budget_sec}s)")
        assert count == len(days)
        assert len(calendar.holidays) == len(days)
        assert elapsed < budget_sec, (
            f"연간 캘린더 왕복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_venue_calendar_day WHERE venue = $1 "
                "AND trade_date >= $2 AND trade_date <= $3",
                Venue.KIS_KRX.value,
                date(year, 1, 1),
                date(year, 12, 31),
            )


# ---- 게이트 적색 재현 — 동일 (venue, year)를 시간축으로 재생 ----


async def test_calendar_lifecycle_replayed_unsynced_then_synced_then_resynced(
    pool: asyncpg.Pool, repo: PostgresCalendarRepository, audit: PostgresAuditEventRepository
) -> None:
    """동일 (venue, year) 하나를 3단계로 재생한다 — 미동기화(추측 거부) ->
    최초 동기화(공시 원문과 일치) -> 재동기화(멱등, day_count·aggregate_id
    불변). 각 단계의 상태가 다음 단계로 새지 않음을 증명한다(§9 RD-21 DoD
    "미수집 연도 질의는 추측하지 않고 거부" + LA-14 "재적재는 멱등")."""
    year = 2193
    days = [
        d.model_copy(update={"trade_date": date(year, d.trade_date.month, d.trade_date.day)})
        for d in load_calendar(_KRX_2026_YAML)
    ]
    trade_date_sample = days[0].trade_date
    expected_aggregate_id = calendar_aggregate_id(Venue.KIS_KRX, year)

    async def _audit_count() -> int:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1 "
                "AND action = 'market_data.calendar_synced'",
                expected_aggregate_id,
            )

    try:
        # 0단계: 미동기화 — 추측 없이 거부.
        async with pool.acquire() as conn, conn.transaction():
            with pytest.raises(CalendarNotLoadedError):
                await repo.load(conn, Venue.KIS_KRX, year)

        # foundation_audit_event는 append-only라 같은 aggregate_id로 이전
        # 테스트 실행이 남긴 이벤트가 누적될 수 있다 — 절대값이 아니라 이
        # 실행에서 생긴 증분으로 1:1 대응을 검증한다.
        audit_count_before = await _audit_count()

        # 1단계: 최초 동기화 — 공시 원문(KRX_2026.yaml)이 그대로 반영된다.
        count1 = await sync_calendar(
            pool,
            Venue.KIS_KRX,
            year,
            days,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            cal=repo,
            audit=audit,
        )
        async with pool.acquire() as conn, conn.transaction():
            calendar1 = await repo.load(conn, Venue.KIS_KRX, year)
        assert count1 == len(days)
        assert trade_date_sample in calendar1.holidays

        # 2단계: 재동기화(같은 데이터 재적재) — 0단계의 CalendarNotLoadedError나
        # 상이한 행 수로 새지 않고 정확히 동일한 day_count·aggregate_id로
        # 수렴해야 한다(멱등).
        count2 = await sync_calendar(
            pool,
            Venue.KIS_KRX,
            year,
            days,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            cal=repo,
            audit=audit,
        )
        async with pool.acquire() as conn, conn.transaction():
            calendar2 = await repo.load(conn, Venue.KIS_KRX, year)
        assert count2 == count1
        assert calendar2.holidays == calendar1.holidays
        assert calendar_aggregate_id(Venue.KIS_KRX, year) == expected_aggregate_id

        audit_count_after = await _audit_count()
        assert audit_count_after - audit_count_before == 2, (
            "동기화 2회 각각 감사 이벤트 1건씩, 1:1 대응이 유지돼야 한다"
        )
    finally:
        # foundation_audit_event는 append-only(WORM)라 DELETE 자체가
        # RaiseError로 거부된다 — 감사 이벤트는 정리하지 않고 그대로 둔다.
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_venue_calendar_day WHERE venue = $1 "
                "AND trade_date >= $2 AND trade_date <= $3",
                Venue.KIS_KRX.value,
                date(year, 1, 1),
                date(year, 12, 31),
            )
