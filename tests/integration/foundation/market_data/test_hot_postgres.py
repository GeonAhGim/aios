"""DC-13 `HotPostgresStorage` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-13. DoD: (1) `instrument_id` 키 5,000봉 조회 p95를 실DB로 실측
출력, (2) 파티션 경계를 가로지르는 구간 조회가 누락·중복 없이 정렬
반환, (3) 존재하지 않는 instrument_id·빈 구간·미래 구간이 각각 빈
결과(예외 아님)로 처리.

(1)의 절대 지연 200ms 단언은 task-1038(`3ea1fc1`)/task-1059(`9bdcd21`)의
선례(CI 인프라 절대 지연 변동성이 코드 회귀와 무관하게 테스트를 상시
적색으로 만든 사고)를 따라 차단 게이트로 쓰지 않는다 — 대신 (a) 실측
p50/p95를 print로 남기고, (b) 이 어댑터가 `PostgresCandleStore`에
위임해 5,000봉 조회를 여전히 순차 DB 왕복 1회(쿼리 1개)로 처리한다는
구조 회귀 가드(왕복 수 상한)를 차단 게이트로 건다 — 왕복 수가 늘면
그건 이 코드가 만든 회귀이고, 절대 지연 변동은 이 파일이 통제할 수
없는 환경 신호이기 때문이다.
"""
from __future__ import annotations

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

_SAMPLE_ROW_COUNT = 5_000  # DoD "5,000봉"
_TARGET_P95_MS = 200.0  # §9.2 DC-13 목표(운영 목표, 차단 게이트 아님 — 모듈 docstring)
_MAX_ROUND_TRIPS = 1  # HotPostgresStorage.read_columns()는 SELECT 1회여야 한다
_MEASURE_SAMPLES = 20

_CANDLE_COLUMNS = (
    "venue", "instrument_id", "timeframe", "open_time", "close_time",
    "open", "high", "low", "close", "volume", "quote_volume", "batch_id",
)


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


async def _instrument_id(conn: asyncpg.Connection, prefix: str = "DC13") -> uuid.UUID:
    symbol = f"{prefix}-{uuid.uuid4().hex}"
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


async def test_read_columns_across_partition_boundary_sorted_no_gaps_no_dupes(
    pool, hot_storage, batch_repo
):
    async with pool.acquire() as conn, conn.transaction():
        await hot_storage.ensure_partitions(conn, months_ahead=6)
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        t1 = t0 + timedelta(days=40)  # 어떤 시작일이든 최소 한 달 파티션 경계를 넘는다
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t1 + timedelta(minutes=1), accepted=2,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candles = [
            _candle(key, t0, 100, 110, 90, 105, 10),
            _candle(key, t1, 200, 210, 190, 205, 20),
        ]
        inserted = await hot_storage.write_batch(conn, batch.batch_id, candles)
    assert inserted == 2

    async with pool.acquire() as conn, conn.transaction():
        columns = await hot_storage.read_columns(
            conn, instrument_id, Venue.BITGET, Timeframe.M1,
            t0 - timedelta(minutes=1), t1 + timedelta(minutes=1),
        )

    assert columns.ts == [t0, t1], "파티션 경계를 넘는 구간이 정렬된 채 누락·중복 없이 와야 한다"
    assert len(columns.ts) == len(set(columns.ts)), "중복 open_time이 없어야 한다"


async def test_read_columns_nonexistent_instrument_returns_empty_not_error(pool, hot_storage):
    async with pool.acquire() as conn, conn.transaction():
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        columns = await hot_storage.read_columns(
            conn, uuid.uuid4(), Venue.BITGET, Timeframe.M1,
            t0 - timedelta(days=1), t0 + timedelta(days=1),
        )
    assert len(columns) == 0
    assert columns.ts == []


async def test_read_columns_empty_range_returns_empty_not_error(pool, hot_storage, batch_repo):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1), accepted=0,
        )
        # start == end: 반개구간 [start, end)이 공집합이라 조회 결과도 공집합이어야 한다.
        columns = await hot_storage.read_columns(
            conn, instrument_id, Venue.BITGET, Timeframe.M1, t0, t0
        )
    assert len(columns) == 0


async def test_read_columns_future_range_returns_empty_not_error(pool, hot_storage, batch_repo):
    async with pool.acquire() as conn, conn.transaction():
        await hot_storage.ensure_partitions(conn, months_ahead=3)
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1), accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candle = _candle(key, t0, 100, 110, 90, 105, 10)
        await hot_storage.write_batch(conn, batch.batch_id, [candle])

        far_future_start = t0 + timedelta(days=365 * 5)
        columns = await hot_storage.read_columns(
            conn, instrument_id, Venue.BITGET, Timeframe.M1,
            far_future_start, far_future_start + timedelta(days=1),
        )
    assert len(columns) == 0, "저장된 캔들보다 훨씬 미래인 구간은 빈 결과여야 한다(예외 아님)"


async def _seed_5000_candles(
    pool, batch_repo, *, instrument_id: uuid.UUID, t0: datetime
) -> None:
    """`_SAMPLE_ROW_COUNT`개의 연속 1분봉을 COPY로 적재한다
    (`test_perf_replay._seed_candles`와 동일 근거 — 파라미터화 멀티행
    INSERT는 이 행수(5,000×12≈60,000 바인드)에서도 asyncpg/PostgreSQL
    단일 쿼리 바인드 상한(65,535)에 위험하게 근접해, 시딩은 상한과 무관한
    COPY로 우회한다. 측정 대상은 시딩이 아니라 `read_columns()`뿐이다)."""
    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(1)")

    range_end = t0 + timedelta(minutes=_SAMPLE_ROW_COUNT)
    async with pool.acquire() as conn, conn.transaction():
        audit_event_id = await _audit_event_id(conn)
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET,
            instrument_id=instrument_id, timeframe=Timeframe.M1, range_start=t0,
            range_end=range_end, request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=_SAMPLE_ROW_COUNT,
                                    quarantined=0, rejected=0, issues=[]),
            batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id,
            stored_range=None,
        )
        await batch_repo.create(conn, batch)

        records = (
            (
                Venue.BITGET.value, instrument_id, Timeframe.M1.value,
                t0 + timedelta(minutes=i), t0 + timedelta(minutes=i + 1),
                Decimal("100"), Decimal("110"), Decimal("90"), Decimal("105"), Decimal("10"),
                None, batch.batch_id,
            )
            for i in range(_SAMPLE_ROW_COUNT)
        )
        await conn.copy_records_to_table("md_candle", records=records, columns=_CANDLE_COLUMNS)


async def _round_trip_count(
    pool, hot_storage, *, instrument_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        async with conn.transaction():
            conn.add_query_logger(_log)
            try:
                await hot_storage.read_columns(
                    conn, instrument_id, Venue.BITGET, Timeframe.M1, start, end
                )
            finally:
                conn.remove_query_logger(_log)
    return len(queries)


@pytest.mark.perf
async def test_read_columns_5000_candles_p95_measured(pool, hot_storage, batch_repo):
    """§9.2 DC-13 DoD: instrument_id 키 5,000봉 조회 p95를 실DB로 실측
    출력한다. 200ms는 운영 목표로 print에 남기되(모듈 docstring), 차단
    게이트는 순차 DB 왕복 수(<=1)로만 건다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, prefix="DC13PERF")
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    await _seed_5000_candles(pool, batch_repo, instrument_id=instrument_id, t0=t0)
    end = t0 + timedelta(minutes=_SAMPLE_ROW_COUNT)

    round_trip_count = await _round_trip_count(
        pool, hot_storage, instrument_id=instrument_id, start=t0, end=end
    )

    latencies_ms: list[float] = []
    for _ in range(_MEASURE_SAMPLES):
        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            columns = await hot_storage.read_columns(
                conn, instrument_id, Venue.BITGET, Timeframe.M1, t0, end
            )
        latencies_ms.append((time.perf_counter() - started) * 1000)
        assert len(columns) == _SAMPLE_ROW_COUNT

    latencies_ms.sort()
    p50_ms = latencies_ms[int(len(latencies_ms) * 0.50)]
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]

    print(
        f"\nhot_postgres read_columns latency ({_SAMPLE_ROW_COUNT} candles): "
        f"p50={p50_ms:.3f}ms p95={p95_ms:.3f}ms (n={len(latencies_ms)}); "
        f"target={_TARGET_P95_MS}ms(운영 목표, 비차단); "
        f"sequential DB round trips={round_trip_count} (max={_MAX_ROUND_TRIPS})"
    )

    assert round_trip_count <= _MAX_ROUND_TRIPS, (
        f"read_columns 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    # 절대 지연 p95 단언은 차단 게이트로 쓰지 않는다(task-1038 `3ea1fc1`/
    # task-1059 `9bdcd21` 선례, 모듈 docstring) — CI 인프라 절대 지연
    # 변동성이 코드 회귀와 무관하게 이 테스트를 상시 적색으로 만들 수
    # 있어서다. 왕복 수 단언만 차단 게이트로 남기고 지연은 위 print로
    # 실측치를 남긴다.
