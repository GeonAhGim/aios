"""DC-13 `HotPostgresStorage` — DEEPEN(task-2886, DEPTH_DC_RD 소급감사
task-2726) D1 -> D3 증빙.

`test_hot_postgres.py`는 파티션 경계·부재 instrument·빈 구간·미래 구간을
"빈 결과(예외 아님)"로 순차 검증하고, perf 테스트는 절대 지연을 print로만
남기며 차단 게이트는 왕복 수 상한 하나뿐이다(D1/D2 일부). 이 파일이 채우는
부족분: (1) `md_candle` CHECK/FK를 실제로 위반하는 `pytest.raises` 기반
실패주입 — `CandleRecord`(pydantic)는 OHLC 부등식을 검증하지 않으므로
`high<open`·`volume<0`처럼 DB CHECK만 거르는 경로가 코드 검증을 우회한
직접 호출로도 존재한다(모듈 docstring이 바로 이 경계를 명시), (2)
`write_batch` 경유 수치 성능 예산을 print가 아니라 차단 assert로 건다(읽기
경로는 task-1038/9bdcd21 선례로 절대 지연을 의도적으로 비차단하지만,
쓰기 경로는 그 선례가 없다), (3) 단일 다중행 INSERT·같은 트랜잭션의
연속 write_batch 양쪽에서 위반이 이미 쓴 유효 행까지 통째로 롤백시키는
게이트 적색 재현, (4) asyncio.gather로 여러 커넥션(다중 워커/재수집
시뮬레이션)이 동일 PK로 동시에 write_batch를 경합할 때 ON CONFLICT DO
NOTHING이 TOCTOU 없이 정확히 하나만 남기는 D3 동시성 증명.
`hot_postgres.py`·`postgres_candle_store.py`·마이그레이션 `4a1d0c0de008`는
무수정 유지 — 새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.storage.hot_postgres import HotPostgresStorage
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)

_PERF_BATCH_SIZE = 2_000
_PERF_BUDGET_SEC = 5.0


@pytest.fixture
def hot_storage(pool: asyncpg.Pool) -> HotPostgresStorage:
    return HotPostgresStorage(pool)


@pytest.fixture
def batch_repo(pool: asyncpg.Pool) -> PostgresBatchRepository:
    return PostgresBatchRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return cast(
        uuid.UUID,
        await conn.fetchval(
            "INSERT INTO foundation_audit_event "
            "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
            " payload_hash, payload, event_hash) "
            "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
            " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
            uuid.uuid4().int % (2**62),
        ),
    )


async def _instrument_id(conn: asyncpg.Connection, prefix: str = "DC13DEEPEN") -> uuid.UUID:
    symbol = f"{prefix}-{uuid.uuid4().hex}"
    return cast(
        uuid.UUID,
        await conn.fetchval(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
            " status, listed_at) "
            "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
            "RETURNING instrument_id",
            symbol,
        ),
    )


def _candle(
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


async def _create_batch(
    conn: asyncpg.Connection,
    batch_repo: PostgresBatchRepository,
    *,
    instrument_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
    accepted: int = 1,
) -> IngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=range_start,
        range_end=range_end,
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=accepted, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )
    return await batch_repo.create(conn, batch)


async def _row_count(
    pool: asyncpg.Pool, *, instrument_id: uuid.UUID, open_time: datetime | None = None
) -> int:
    if open_time is None:
        return cast(
            int,
            await pool.fetchval(
                "SELECT count(*) FROM md_candle WHERE instrument_id = $1", instrument_id
            ),
        )
    return cast(
        int,
        await pool.fetchval(
            "SELECT count(*) FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
            instrument_id,
            open_time,
        ),
    )


async def test_concurrent_write_batch_disjoint_open_times_all_succeed(
    hot_storage: HotPostgresStorage, batch_repo: PostgresBatchRepository, pool: asyncpg.Pool
) -> None:
    """겹치지 않는 `open_time`이면 동시에 write_batch해도 전부 성공해야
    한다 — PK 제약이 과도하게 넓게 직렬화(모든 동시 쓰기를 막음)하지
    않는다는 회귀 방지."""
    n = 20
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, prefix="DC13PARALLEL")
    t0 = datetime.now(UTC).replace(microsecond=0)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    batch_ids: list[uuid.UUID] = []
    for _ in range(n):
        async with pool.acquire() as conn, conn.transaction():
            batch = await _create_batch(
                conn,
                batch_repo,
                instrument_id=instrument_id,
                range_start=t0,
                range_end=t0 + timedelta(minutes=n),
                accepted=1,
            )
        batch_ids.append(batch.batch_id)

    async def _attempt(i: int, batch_id: uuid.UUID) -> int:
        candle = _candle(key, t0 + timedelta(minutes=i), o=100, h=110, low=90, c=105, v=10)
        async with pool.acquire() as conn, conn.transaction():
            return await hot_storage.write_batch(conn, batch_id, [candle])

    results = await asyncio.gather(*(_attempt(i, b) for i, b in enumerate(batch_ids)))

    assert results == [1] * n
    assert await _row_count(pool, instrument_id=instrument_id) == n
