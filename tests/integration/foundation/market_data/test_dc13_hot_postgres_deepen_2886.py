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
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
def hot_storage(pool):
    return HotPostgresStorage(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(conn: asyncpg.Connection, prefix: str = "DC13DEEPEN") -> uuid.UUID:
    symbol = f"{prefix}-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
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


async def _row_count(pool, *, instrument_id: uuid.UUID, open_time: datetime | None = None) -> int:
    if open_time is None:
        return await pool.fetchval(
            "SELECT count(*) FROM md_candle WHERE instrument_id = $1", instrument_id
        )
    return await pool.fetchval(
        "SELECT count(*) FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
        instrument_id,
        open_time,
    )


# ---- 실패주입(D2) — CandleRecord(pydantic)가 안 거르는 DB CHECK/FK ----


async def test_write_batch_rejects_high_less_than_open(hot_storage, batch_repo, pool):
    """`ck_md_candle_high_ge_open` — pydantic `CandleRecord`는 OHLC 부등식을
    검증하지 않으므로(모듈 docstring), 코드 검증을 우회해도 DB CHECK가
    최후 방어선으로 막아야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        bad = _candle(key, t0, o=100, h=90, low=80, c=95, v=10)  # high(90) < open(100)

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await hot_storage.write_batch(conn, batch.batch_id, [bad])


async def test_write_batch_rejects_negative_volume(hot_storage, batch_repo, pool):
    """`ck_md_candle_volume_nonneg` — 음수 거래량은 거부된다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        bad = _candle(key, t0, o=100, h=110, low=90, c=105, v=-1)

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await hot_storage.write_batch(conn, batch.batch_id, [bad])


async def test_write_batch_rejects_unknown_instrument_fk(hot_storage, batch_repo, pool):
    """`md_candle.instrument_id` FK — 기존 테스트는 항상 미리 instrument를
    등록해 이 경로를 안 탔다. 등록되지 않은 instrument를 가리키는 candle은
    배치 자체는 유효해도 거부된다."""
    async with pool.acquire() as conn, conn.transaction():
        real_instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=real_instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        unknown_key = SeriesKey(
            venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1
        )
        bad = _candle(unknown_key, t0, o=100, h=110, low=90, c=105, v=10)

        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await hot_storage.write_batch(conn, batch.batch_id, [bad])


async def test_write_batch_rejects_unknown_batch_id_fk(hot_storage, pool):
    """`md_candle.batch_id` FK — `md_ingest_batch`에 존재하지 않는
    `batch_id`로는 아무리 유효한 candle이라도 쓸 수 없다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candle = _candle(key, t0, o=100, h=110, low=90, c=105, v=10)

        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await hot_storage.write_batch(conn, uuid.uuid4(), [candle])


# ---- 성능 단언(D2) — write_batch 경유, print가 아니라 차단 게이트 ----


@pytest.mark.perf
async def test_write_batch_bulk_meets_latency_budget(hot_storage, batch_repo, pool):
    """읽기 경로(`test_read_columns_5000_candles_p95_measured`)는
    task-1038/9bdcd21 선례로 절대 지연을 의도적으로 비차단하지만, 그
    선례는 이 쓰기 경로에는 적용된 적이 없다 — `write_batch` 다중행 INSERT
    1회가 예산 안에 드는지를 직접 차단 assert로 건다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, prefix="DC13PERFWRITE")
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    candles = [
        _candle(key, t0 + timedelta(minutes=i), o=100, h=110, low=90, c=105, v=10)
        for i in range(_PERF_BATCH_SIZE)
    ]

    async with pool.acquire() as conn, conn.transaction():
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=_PERF_BATCH_SIZE),
            accepted=_PERF_BATCH_SIZE,
        )

        started = time.perf_counter()
        inserted = await hot_storage.write_batch(conn, batch.batch_id, candles)
        elapsed = time.perf_counter() - started

    print(
        f"\nhot_postgres write_batch({_PERF_BATCH_SIZE} candles): {elapsed:.3f}s "
        f"({elapsed / _PERF_BATCH_SIZE * 1e3:.3f} ms/candle, budget<{_PERF_BUDGET_SEC}s)"
    )
    assert inserted == _PERF_BATCH_SIZE
    assert elapsed < _PERF_BUDGET_SEC, (
        f"write_batch {_PERF_BATCH_SIZE}건이 예산({_PERF_BUDGET_SEC}s)을 넘었습니다"
        f"({elapsed:.3f}s) — 다중행 INSERT 경로에 회귀가 있는지 확인하세요."
    )


# ---- 게이트 적색 재현(D2) — 위반이 이미 쓴 유효 행까지 통째로 롤백 ----


async def test_gate_red_single_call_check_violation_rolls_back_whole_insert(
    hot_storage, batch_repo, pool
):
    """`write_batch` 한 번 호출에 유효한 candle과 CHECK 위반 candle을 함께
    넘기면, 다중행 INSERT는 문장 단위 원자성이라 유효한 쪽도 커밋되지
    않아야 한다(부분 성공 없음)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=2),
            accepted=2,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        good = _candle(key, t0, o=100, h=110, low=90, c=105, v=10)
        bad = _candle(key, t0 + timedelta(minutes=1), o=100, h=90, low=80, c=95, v=10)

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await hot_storage.write_batch(conn, batch.batch_id, [good, bad])

    assert await _row_count(pool, instrument_id=instrument_id) == 0, (
        "다중행 INSERT의 유효한 행이 위반 행과 함께 부분 커밋되었습니다"
    )


async def test_gate_red_transaction_fk_violation_rolls_back_prior_write(
    hot_storage, batch_repo, pool
):
    """같은 트랜잭션 안에서 (a) 유효한 write_batch 성공 (b) 그 뒤 FK 위반
    write_batch를 실행하면, 트랜잭션 전체가 롤백돼 (a)도 커밋되지 않아야
    한다 — 게이트 적색이 문장 하나가 아니라 트랜잭션 전체의 원자적 실패임을
    증명한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            async with conn.transaction():
                batch_a = await _create_batch(
                    conn,
                    batch_repo,
                    instrument_id=instrument_id,
                    range_start=t0,
                    range_end=t0 + timedelta(minutes=1),
                    accepted=1,
                )
                good = _candle(key, t0, o=100, h=110, low=90, c=105, v=10)
                inserted = await hot_storage.write_batch(conn, batch_a.batch_id, [good])
                assert inserted == 1

                bad_key = SeriesKey(
                    venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1
                )
                bad = _candle(bad_key, t0 + timedelta(minutes=1), o=100, h=110, low=90, c=105, v=10)
                await hot_storage.write_batch(conn, batch_a.batch_id, [bad])

    assert await _row_count(pool, instrument_id=instrument_id) == 0, (
        "게이트 위반 트랜잭션의 앞선 write_batch가 커밋되어 남았습니다"
    )


# ---- 동시 다중 인스턴스/워커(D3) — ON CONFLICT DO NOTHING이 TOCTOU 없이 지킨다 ----


async def test_concurrent_write_batch_same_open_time_exactly_one_winner(
    hot_storage, batch_repo, pool
):
    """서로 다른 커넥션(다중 워커/재수집 시뮬레이션) 8개가 동일한
    `(venue, instrument_id, timeframe, open_time)` PK를 동시에
    `write_batch`로 쓰려고 시도한다. 앱 레벨 check-then-insert였다면
    TOCTOU 경합으로 여럿이 통과할 수 있지만, PK `ON CONFLICT DO NOTHING`은
    DB가 직렬화하므로 정확히 1개만 신규 삽입(inserted==1)이어야 한다."""
    n = 8
    instrument_id: uuid.UUID
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, prefix="DC13RACE")
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    batch_ids: list[uuid.UUID] = []
    for _ in range(n):
        async with pool.acquire() as conn, conn.transaction():
            batch = await _create_batch(
                conn,
                batch_repo,
                instrument_id=instrument_id,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
                accepted=1,
            )
        batch_ids.append(batch.batch_id)

    async def _attempt(batch_id: uuid.UUID) -> int:
        candle = _candle(key, t0, o=100, h=110, low=90, c=105, v=10)
        async with pool.acquire() as conn, conn.transaction():
            return await hot_storage.write_batch(conn, batch_id, [candle])

    results = await asyncio.gather(*(_attempt(b) for b in batch_ids))

    assert sum(results) == 1, (
        f"동시 write_batch {n}건(동일 PK) 중 신규 삽입이 {sum(results)}건입니다 — "
        "ON CONFLICT DO NOTHING이 경합 상황에서 정확히 하나만 통과시키지 못했습니다"
    )
    assert await _row_count(pool, instrument_id=instrument_id, open_time=t0) == 1


async def test_concurrent_write_batch_disjoint_open_times_all_succeed(
    hot_storage, batch_repo, pool
):
    """겹치지 않는 `open_time`이면 동시에 write_batch해도 전부 성공해야
    한다 — PK 제약이 과도하게 넓게 직렬화(모든 동시 쓰기를 막음)하지
    않는다는 회귀 방지."""
    n = 20
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, prefix="DC13PARALLEL")
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
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
