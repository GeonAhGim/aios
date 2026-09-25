"""PostgresCandleStore CHECK 제약·빈 범위 조회 — test_candle_store.py에서 분할.

CTO 2026-09-23, loc_over_500 래칫(ddb6ebea 504줄). 헬퍼는 candle_store_support.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import (
    SeriesKey,
    Timeframe,
    Venue,
)
from tests.integration.foundation.market_data.candle_store_support import (
    _candle,
    _create_batch,
    _instrument_id,
)


async def test_upsert_batch_rejects_low_below_high_check_violation(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: low > high인 캔들은 md_candle의 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 120, 125, 110, 10)  # low(125) > high(120)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_high_ge_low"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_upsert_batch_rejects_close_outside_range_check_violation(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: close가 high와 low 사이에 없으면 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 120, 90, 130, 10)  # close(130) > high(120)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_upsert_batch_rejects_negative_volume(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: 음수 거래량은 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 110, 90, 105, -5)  # 음수 거래량
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_volume"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_query_with_empty_time_range(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: start > end인 시간 범위 조회는 빈 결과를 반환해야 한다."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    t1 = t0 + timedelta(minutes=5)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t1 + timedelta(minutes=1),
        )
        candle = _candle(key, t0, 100, 110, 90, 105, 10)
        await candle_store.upsert_batch(conn, batch.batch_id, [candle])

    async with pool.acquire() as conn, conn.transaction():
        # start > end인 범위로 조회
        result = await candle_store.query(
            conn,
            key,
            t1,
            t0,
            as_of=None,  # reversed time range
        )
    assert len(result) == 0, "반전된 시간 범위는 빈 결과를 반환해야 한다"
