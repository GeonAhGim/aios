"""LA-17 `get_candles` 통합테스트 공용 시드 헬퍼.

task-10929: `test_get_candles.py` 분할(loc_over_500)로 핵심/실패주입/성능
테스트 모듈이 공유하는 시드 로직 — 본 모듈은 pytest가 테스트로 수집하지
않는다(`perf_replay_support.py`/`candle_store_support.py`/
`ingest_candles_support.py`와 동일한 디렉터리 관례, `test_` 접두사 없음).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)


async def audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def candle(
    key: SeriesKey, open_time: datetime, o: float, h: float, low: float, c: float, v: float
) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


async def seed_candle(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id: uuid.UUID, open_time
) -> SeriesKey:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    event_id = await audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=open_time,
        range_end=open_time + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=event_id,
        stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(
        conn, batch.batch_id, [candle(key, open_time, 100, 110, 90, 105, 10)]
    )
    return key


async def candle_open_time_before_today(conn: asyncpg.Connection, as_of: datetime) -> datetime:
    """A candle whose (UTC) date is strictly earlier than `as_of`'s date.

    Normally that is the first of the current month. On the 1st itself the month start IS
    today, so `ex_date == candle date` and no adjustment applies -- this test went red on every
    first day of a month (CI 2026-10-01). Then use yesterday instead, creating the previous
    month's `md_candle` partition (the schema only pre-creates current and future months).
    """
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if as_of.day > 1:
        return month_start
    await conn.execute(
        """
        DO $$
        DECLARE
            part_end TIMESTAMPTZ := date_trunc('month', now());
            part_start TIMESTAMPTZ := part_end - interval '1 month';
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS md_candle_%s PARTITION OF md_candle '
                'FOR VALUES FROM (%L) TO (%L)',
                to_char(part_start, 'YYYY_MM'), part_start, part_end
            );
        END $$;
        """
    )
    return month_start - timedelta(days=1)


def utc_minute_now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)
