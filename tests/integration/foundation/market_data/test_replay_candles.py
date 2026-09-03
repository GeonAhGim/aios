"""LA-17 `application/replay_candles.replay` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-17, A5.
DoD(task-624): 해시 결정론(같은 입력 → 같은 바이트), strict 모드에서 갭이
있으면 예외(negative), 미등록 instrument → 명시적 에러(negative).

BITGET(연속 세션)만 쓴다 — `get_candles.py`와 동일 이유로 캘린더 시드가
필요 없다.
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
from src.foundation.market_data.application.get_candles import UnknownSeriesError
from src.foundation.market_data.application.replay_candles import ReplayIncompleteError, replay
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    ReplayRequest,
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


async def _seed_candles(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id, key, opens
) -> None:
    audit_event_id = await _audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET, instrument_id=instrument_id,
        timeframe=Timeframe.M1, range_start=opens[0], range_end=opens[-1] + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=len(opens), quarantined=0,
                                rejected=0, issues=[]),
        batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id, stored_range=None,
    )
    await batch_repo.create(conn, batch)
    candles = [_candle(key, ot, 100, 110, 90, 105, 10) for ot in opens]
    await candle_store.upsert_batch(conn, batch.batch_id, candles)


async def test_replay_series_hash_is_deterministic_across_calls(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        opens = [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)]
        await _seed_candles(conn, batch_repo, candle_store, instrument_id=instrument_id, key=key,
                             opens=opens)
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=3), as_of=as_of)
    first = await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo,
                          pool=pool)
    second = await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo,
                           pool=pool)

    assert first.series_hash == second.series_hash
    assert first.expected_count == 3
    assert first.missing_count == 0
    assert first.gaps == []


async def test_replay_strict_gap_raises_incomplete(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: strict 모드는 갭이 있으면 결과를 반환하지 않고 예외를 낸다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # t0+1분을 건너뛴다 — 3분짜리 창에 2개만 저장.
        opens = [t0, t0 + timedelta(minutes=2)]
        await _seed_candles(conn, batch_repo, candle_store, instrument_id=instrument_id, key=key,
                             opens=opens)
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=3), as_of=as_of)
    with pytest.raises(ReplayIncompleteError) as exc_info:
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo,
                      pool=pool)
    assert exc_info.value.expected_count == 3
    assert exc_info.value.missing_count == 1


async def test_replay_unknown_series_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: 한 번도 수집된 적 없는 시계열 → 명시적 에러(리플레이도 동일)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=t0)
    with pytest.raises(UnknownSeriesError):
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo,
                      pool=pool)
