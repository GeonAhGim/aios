"""LA-21 계약·성능 — 리플레이 525,600행(1분봉 1년치) < 5s(실 DB).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9.2 LA-21
("리플레이 525k행 < 5s" — "벤치마크에 단언 없음"을 반복하지 않도록 아래에
실제 `assert`를 건다).

측정 대상은 `application/replay_candles.replay()` 단독 호출이다. 캔들
525,600행 시딩은 타이밍에 포함하지 않는다(운영 경로에서 리플레이는 이미
저장된 캔들을 읽기만 하고, 시딩 비용은 이 리프가 최적화할 대상이
아니다) — 다만 시딩 자체도 `asyncpg.Connection.copy_records_to_table`로
한 번에 적재한다: `PostgresCandleStore.upsert_batch()`가 만드는 파라미터화
멀티행 INSERT는 이 규모(525,600행 × 12컬럼 ≈ 630만 바인드 파라미터)에서
PostgreSQL 프로토콜의 단일 쿼리 바인드 파라미터 상한(65,535)을 훨씬
넘겨 그대로 쓸 수 없다 — COPY는 이 시딩 전용 우회이고 리플레이 자체
경로(`candle_store.query`)는 손대지 않는다.

**실결함(task-655 발견, decision, needs 최적화 task-826)**: 이 환경
(TEST_DATABASE_URL, localhost)에서 실측한 `replay()` 단독 호출 시간은
목표(5s)를 크게 초과한다(정확한 실측치는 아래 `xfail reason`과 task-655
`tests` 필드에 남긴다). 원인은 `application/get_candles.load_series`가
`CandleStore.query()`로 525,600행 전체를 메모리에 올린 뒤
`domain/lineage.batch_hash`(레코드마다 canonical JSON 직렬화 후 전체
정렬)로 series_hash를 계산하는 구조로 보인다 — 왕복 수 자체는 1회뿐이라
`asyncpg` 왕복 축소로는 해결되지 않고, 대량 레코드의 직렬화·정렬 비용을
줄이는 별도 설계가 필요하다(task-826 스코프, 이 리프에서 손대지 않는다).
스펙이 요구하는 목표는 그대로 걸고, 실패 자체를 `xfail(strict=False)`로
고정해(decision, ledger LC-17/task-614 선례와 달리 strict=True로 CI를
적색으로 만들지 않는다) "벤치마크에 단언 없음"을 피하면서도 스위트를
통과시킨다.
"""
from __future__ import annotations

import time
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
from src.foundation.market_data.application.replay_candles import replay
from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityVerdict,
    ReplayRequest,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)

_ROW_COUNT = 525_600  # 60(분) × 24(시) × 365(일) — 1분봉 1년치, §8.4 "525k행"
_TARGET_SECONDS = 5.0
_CANDLE_COLUMNS = (
    "venue", "instrument_id", "timeframe", "open_time", "close_time",
    "open", "high", "low", "close", "volume", "quote_volume", "batch_id",
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
    symbol = f"PERF-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def _row(instrument_id: uuid.UUID, batch_id: uuid.UUID, open_time: datetime) -> tuple:
    return (
        Venue.BITGET.value, instrument_id, Timeframe.M1.value, open_time,
        open_time + timedelta(minutes=1), Decimal("100"), Decimal("110"), Decimal("90"),
        Decimal("105"), Decimal("10"), None, batch_id,
    )


async def _seed_year_of_candles(
    pool, batch_repo, *, instrument_id: uuid.UUID, t0: datetime
) -> tuple[IngestBatchResult, datetime]:
    """`_ROW_COUNT`개의 연속 1분봉을 COPY로 적재한다(모듈 docstring —
    파라미터화 INSERT는 이 규모에서 바인드 파라미터 상한을 넘긴다).

    `md_candle`은 월별 RANGE 파티션(LA-11)이고 `md_ensure_partitions
    (months_ahead)`(마이그레이션 `4a1d0c0de008`)는 **현재 월부터 미래로만**
    파티션을 만든다 — 과거로 시딩하면 파티션이 없어 INSERT가
    `CheckViolationError: no partition of relation ... found`로 거부된다.
    그래서 `t0`는 항상 현재 시각 이후여야 하고, 시딩 전에 이 함수를 호출해
    `_ROW_COUNT`(약 365일)를 덮을 만큼(13개월치) 파티션을 미리 만든다.
    `SECURITY DEFINER`(모듈 마이그레이션 docstring)라 `aios_app` 권한으로도
    호출 가능하다."""
    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(13)")

    range_end = t0 + timedelta(minutes=_ROW_COUNT)
    async with pool.acquire() as conn, conn.transaction():
        audit_event_id = await _audit_event_id(conn)
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET,
            instrument_id=instrument_id, timeframe=Timeframe.M1, range_start=t0,
            range_end=range_end, request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=_ROW_COUNT, quarantined=0,
                                    rejected=0, issues=[]),
            batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id,
            stored_range=None,
        )
        await batch_repo.create(conn, batch)

        records = (
            _row(instrument_id, batch.batch_id, t0 + timedelta(minutes=i))
            for i in range(_ROW_COUNT)
        )
        await conn.copy_records_to_table("md_candle", records=records, columns=_CANDLE_COLUMNS)

        as_of = await conn.fetchval("SELECT now()")
    return batch, as_of


@pytest.mark.perf
@pytest.mark.xfail(
    strict=False,
    reason=(
        "실결함(task-655 발견, decision, needs_optimization task-826): "
        "이 환경(TEST_DATABASE_URL, localhost)에서 525,600행 리플레이 "
        "단독 호출 실측 39.893s — §8.4 목표(5s)를 크게 초과한다. 원인은 "
        "load_series()가 전체 행을 메모리에 올려 batch_hash로 canonical "
        "JSON 직렬화+정렬하는 구조로 추정(모듈 docstring 참고) — 왕복 수"
        "축소가 아니라 별도 설계가 필요해 이 리프(LA-21) 범위 밖이다. "
        "임계를 완화하지 않고 xfail(strict=False)로 CI를 적색으로 "
        "만들지 않는다(decision, ledger LC-17/task-614는 strict=True를 "
        "썼지만 여기서는 decision이 명시적으로 strict=False를 지정했다)."
    ),
)
async def test_replay_525600_candles_under_5s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    # `_seed_year_of_candles`가 `md_ensure_partitions`로 현재 월부터 미래로만
    # 파티션을 만들 수 있으므로(함수 docstring), t0는 현재 이후여야 한다.
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)

    _, as_of = await _seed_year_of_candles(pool, batch_repo, instrument_id=instrument_id, t0=t0)

    request = ReplayRequest(
        key=key, start=t0, end=t0 + timedelta(minutes=_ROW_COUNT), as_of=as_of
    )

    started = time.perf_counter()
    series = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    elapsed_seconds = time.perf_counter() - started

    print(
        f"\nmarket_data replay latency: {elapsed_seconds:.3f}s "
        f"(rows={series.expected_count}, target<{_TARGET_SECONDS}s)"
    )

    assert series.expected_count == _ROW_COUNT
    assert series.missing_count == 0
    assert elapsed_seconds < _TARGET_SECONDS, (
        f"리플레이 {_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"목표({_TARGET_SECONDS}s)를 초과했습니다."
    )
