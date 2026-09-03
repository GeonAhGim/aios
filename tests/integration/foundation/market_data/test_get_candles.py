"""LA-17 `application/get_candles.get_candles` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-17.
DoD(task-624): 조정계수는 as_of 시점 기준으로만 반영, 갭은 정보로만 반환(비
strict), negative: 미등록 instrument → 명시적 에러.

BITGET(연속 세션)만 쓴다 — `VenueCalendar`(LA-3) 휴장일 적재 없이도 세션
판정이 가능해 캘린더 시드가 필요 없다(get_candles.py의 `_sessions_for_range`
가 연속 venue는 `CalendarRepository`를 아예 호출하지 않는다).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.get_candles import (
    AsOfInFutureError,
    QuarantinedViewUnsupportedError,
    UnknownSeriesError,
    get_candles,
)
from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    CandleRecord,
    CorporateAction,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


@pytest.fixture
def reference_repo(pool):
    return PostgresReferenceRepository(pool)


@pytest.fixture
def calendar_repo(pool):
    return PostgresCalendarRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def _candle(key: SeriesKey, open_time: datetime, o: float, h: float, low: float, c: float,
            v: float) -> CandleRecord:
    return CandleRecord(
        key=key, open_time=open_time, close_time=open_time + timedelta(minutes=1),
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(low)),
        close=Decimal(str(c)), volume=Decimal(str(v)),
    )


async def _seed_candle(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id, open_time
) -> SeriesKey:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    audit_event_id = await _audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET, instrument_id=instrument_id,
        timeframe=Timeframe.M1, range_start=open_time, range_end=open_time + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0,
                                issues=[]),
        batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id, stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(
        conn, batch.batch_id, [_candle(key, open_time, 100, 110, 90, 105, 10)]
    )
    return key


async def test_get_candles_returns_raw_series_with_hash_and_no_gaps(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(conn, batch_repo, candle_store, instrument_id=instrument_id,
                                  open_time=t0)

    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert [c.open_time for c in series.candles] == [t0]
    assert series.gaps == []
    assert series.adjustment is Adjustment.RAW
    assert len(series.series_hash) == 64


async def test_get_candles_reports_gap_without_raising(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(conn, batch_repo, candle_store, instrument_id=instrument_id,
                                  open_time=t0)

    # t0+1분은 결측 — 3분짜리 창 안에 캔들 1개(t0)만 있으므로 갭 1구간.
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=3))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.gaps == [(t0 + timedelta(minutes=1), t0 + timedelta(minutes=3))]


async def test_get_candles_adjustment_ignores_action_after_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `ex_date`가 `as_of`보다 미래인 조정은 `factor_chain`(LA-8)이
    제외한다 — as_of 시점에는 아직 일어나지 않은 조정이므로."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정 — md_candle 파티션은 현재~+N개월만 존재하므로(과거 달
        # 파티션 없음) 이번 달 안에서만 "과거" 캔들을 만든다.
        t0 = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        key = await _seed_candle(conn, batch_repo, candle_store, instrument_id=instrument_id,
                                  open_time=t0)
        await reference_repo.record_action(conn, CorporateAction(
            action_type="SPLIT", instrument_id=instrument_id,
            ex_date=as_of.date() + timedelta(days=1), ratio=Decimal(2), source_ref="test:future",
        ))

    query = CandleQuery(
        key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("100"), "as_of보다 미래인 ex_date는 미반영"


async def test_get_candles_adjustment_applies_action_before_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """`ex_date`가 `as_of` 이전(<=)이고 캔들 날짜보다는 이후면 조정이 반영된다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정(위 테스트와 같은 이유) — ex_date는 캔들(월초)보다는
        # 늦고 as_of(오늘)보다는 이르거나 같아야 하므로 오늘 날짜를 쓴다.
        t0 = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        key = await _seed_candle(conn, batch_repo, candle_store, instrument_id=instrument_id,
                                  open_time=t0)
        await reference_repo.record_action(conn, CorporateAction(
            action_type="SPLIT", instrument_id=instrument_id,
            ex_date=as_of.date(), ratio=Decimal(2), source_ref="test:past",
        ))

    query = CandleQuery(
        key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("50"), "as_of 이전 ex_date 조정은 반영되어야 한다"
    assert series.candles[0].volume == Decimal("20")


async def test_get_candles_unknown_series_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: 한 번도 수집된 적 없는 (venue, instrument, timeframe) → 명시적 에러."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    with pytest.raises(UnknownSeriesError):
        await get_candles(query, store=candle_store, refs=reference_repo, cal=calendar_repo,
                           pool=pool)


async def test_get_candles_as_of_in_future_raises(
    pool, candle_store, reference_repo, calendar_repo
):
    """negative: `as_of`가 현재보다 미래면 조회 전에 즉시 거부한다(스토어를
    건드리지 않으므로 미등록 instrument여도 무방)."""
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    future_as_of = t0 + timedelta(days=1)
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=future_as_of)
    with pytest.raises(AsOfInFutureError):
        await get_candles(query, store=candle_store, refs=reference_repo, cal=calendar_repo,
                           pool=pool)


async def test_get_candles_quarantined_view_unsupported_raises(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `CandleStore.query`(LA-13)가 지원하지 않는 파라미터는 조용히
    무시하지 않고 명시적으로 거부한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(conn, batch_repo, candle_store, instrument_id=instrument_id,
                                  open_time=t0)

    query = CandleQuery(
        key=key, start=t0, end=t0 + timedelta(minutes=1), include_quarantined=True,
    )
    with pytest.raises(QuarantinedViewUnsupportedError):
        await get_candles(query, store=candle_store, refs=reference_repo, cal=calendar_repo,
                           pool=pool)
