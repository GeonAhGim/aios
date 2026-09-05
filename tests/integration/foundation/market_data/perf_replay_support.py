"""LA-23/LA-23b 리플레이 성능 테스트 공용 헬퍼(시딩·왕복 수 계수).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9.2 LA-23,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md.

`test_perf_replay.py`(기본 CI, 환경 정규화 게이트)와
`test_perf_replay_nightly.py`(nightly, 절대시간 계약)가 공유한다. 이 모듈
자체는 테스트를 담지 않는다(`test_` 접두사 없음 — pytest가 수집하지 않는다).

**시딩(`seed_candles`)**: 캔들은 `asyncpg.Connection.copy_records_to_table`로
한 번에 적재한다. `PostgresCandleStore.upsert_batch()`가 만드는 파라미터화
멀티행 INSERT는 연단위 규모(525,600행 × 12컬럼 ≈ 630만 바인드 파라미터)에서
PostgreSQL 프로토콜의 단일 쿼리 바인드 파라미터 상한(65,535)을 훨씬 넘겨
그대로 쓸 수 없다 — COPY는 이 시딩 전용 우회이고 리플레이 자체 경로
(`candle_store.read_candles_columnar`)는 손대지 않는다. 시딩 비용은 어느
테스트에서도 타이밍에 포함하지 않는다(운영 경로에서 리플레이는 이미 저장된
캔들을 읽기만 한다).

**왕복 수 계수(`count_replay_round_trips`)**: task-1038/3ea1fc1(ledger
`test_perf_journal`)·DC-13(`test_hot_postgres`) 선례와 같은 asyncpg
`add_query_logger` 방식이다. `application/replay_candles.replay()`는 풀에서
직접 커넥션을 얻으므로(`pool.acquire()`), 계수용으로 미리 얻어 둔 커넥션
하나만 돌려주는 풀 대역(`_PinnedConnectionPool`)을 넘겨 **같은 커넥션**에서
워밍업 1회 → 계수 1회를 순서대로 흘린다. 워밍업이 필요한 이유는 선례와
같다 — asyncpg는 처음 보는 커넥션에서 커스텀 타입(enum·numeric 등) 코덱을
알아내려고 내부 조회(`typeinfo_tree` 등)를 몇 회 더 보내는데, 이는 그
커넥션의 평생 1회성 드라이버 오버헤드지 `replay()`가 매 호출 내는 왕복이
아니다.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import asyncpg

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
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "DAY_ROW_COUNT",
    "MONTH_ROW_COUNT",
    "YEAR_ROW_COUNT",
    "count_replay_round_trips",
    "new_instrument_id",
    "seed_candles",
    "series_key",
]

YEAR_ROW_COUNT = 525_600  # 60(분) × 24(시) × 365(일) — 1분봉 1년치, §8.4 "1년"
MONTH_ROW_COUNT = 43_200  # 60(분) × 24(시) × 30(일) — 1분봉 1개월치, §8.4 "1개월"
DAY_ROW_COUNT = 1_440  # 60(분) × 24(시) — 1분봉 1일치, §8.4 "1일"

_CANDLE_COLUMNS = (
    "venue", "instrument_id", "timeframe", "open_time", "close_time",
    "open", "high", "low", "close", "volume", "quote_volume", "batch_id",
)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def new_instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
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


async def seed_candles(
    pool, batch_repo, *, instrument_id: uuid.UUID, t0: datetime, row_count: int
) -> tuple[IngestBatchResult, datetime]:
    """`row_count`개의 연속 1분봉을 COPY로 적재한다(모듈 docstring).

    `md_candle`은 월별 RANGE 파티션(LA-11)이고 `md_ensure_partitions
    (months_ahead)`(마이그레이션 `4a1d0c0de008`)는 **현재 월부터 미래로만**
    파티션을 만든다 — 과거로 시딩하면 파티션이 없어 INSERT가
    `CheckViolationError: no partition of relation ... found`로 거부된다.
    그래서 `t0`는 항상 현재 시각 이후여야 하고, 시딩 전에 이 함수를 호출해
    `YEAR_ROW_COUNT`(약 365일) 규모까지도 덮을 만큼(13개월치) 파티션을 미리
    만든다. `SECURITY DEFINER`(모듈 마이그레이션 docstring)라 `aios_app`
    권한으로도 호출 가능하다."""
    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(13)")

    range_end = t0 + timedelta(minutes=row_count)
    async with pool.acquire() as conn, conn.transaction():
        audit_event_id = await _audit_event_id(conn)
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET,
            instrument_id=instrument_id, timeframe=Timeframe.M1, range_start=t0,
            range_end=range_end, request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=row_count, quarantined=0,
                                    rejected=0, issues=[]),
            batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id,
            stored_range=None,
        )
        await batch_repo.create(conn, batch)

        records = (
            _row(instrument_id, batch.batch_id, t0 + timedelta(minutes=i))
            for i in range(row_count)
        )
        await conn.copy_records_to_table("md_candle", records=records, columns=_CANDLE_COLUMNS)

        as_of = await conn.fetchval("SELECT now()")
    return batch, as_of


class _PinnedConnectionPool:
    """`replay(pool=...)`에 넘기는 풀 대역 — `acquire()`가 항상 미리 얻어 둔
    커넥션 하나를 돌려준다(반납하지 않는다). 워밍업과 계수가 같은
    커넥션에서 일어나야 드라이버 1회성 조회가 계수에 섞이지 않는다(모듈
    docstring). 실제 `asyncpg.Pool`의 `acquire()` 컨텍스트 매니저 계약만
    흉내 낸다."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


async def count_replay_round_trips(
    pool: asyncpg.Pool,
    request: ReplayRequest,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
) -> int:
    """`replay(request)` 1회가 소비하는 순차 DB 왕복 수(구조 회귀 가드).

    같은 커넥션에서 워밍업 호출 1회를 먼저 흘려 asyncpg 코덱 조회를
    흡수시킨 뒤, 두 번째 호출만 쿼리 로거로 센다(모듈 docstring)."""
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        pinned = _PinnedConnectionPool(conn)
        await replay(request, store=store, refs=refs, cal=cal, pool=pinned)  # type: ignore[arg-type]

        conn.add_query_logger(_log)
        try:
            await replay(request, store=store, refs=refs, cal=cal, pool=pinned)  # type: ignore[arg-type]
        finally:
            conn.remove_query_logger(_log)

    return len(queries)


def series_key(instrument_id: uuid.UUID) -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
