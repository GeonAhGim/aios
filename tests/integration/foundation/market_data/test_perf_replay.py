"""LA-21/LA-23/LA-23b 계약·성능 — 리플레이 성능(§8.4 규모별 계약) 검증(실 DB).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9.2 LA-21, LA-23,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md
("벤치마크에 단언 없음"을 반복하지 않도록 아래에 실제 `assert`를 건다).

측정 대상은 `application/replay_candles.replay()` 단독 호출이다. 캔들
시딩은 타이밍에 포함하지 않는다(운영 경로에서 리플레이는 이미 저장된
캔들을 읽기만 하고, 시딩 비용은 이 리프가 최적화할 대상이 아니다) — 다만
시딩 자체도 `asyncpg.Connection.copy_records_to_table`로 한 번에 적재한다:
`PostgresCandleStore.upsert_batch()`가 만드는 파라미터화 멀티행 INSERT는
연단위 규모(525,600행 × 12컬럼 ≈ 630만 바인드 파라미터)에서 PostgreSQL
프로토콜의 단일 쿼리 바인드 파라미터 상한(65,535)을 훨씬 넘겨 그대로 쓸 수
없다 — COPY는 이 시딩 전용 우회이고 리플레이 자체 경로(`candle_store.
read_candles_columnar`)는 손대지 않는다.

**규모별 계약(ADR-2026-09-04-A §8.4, 단일 노드 P95)**: 1일(1,440) ≤ 0.5s,
1개월(43,200) ≤ 5s, 1년(525,600) ≤ 30s.

1. `test_replay_1day_1440_candles_under_5s` — 기준 규모(1일 = M1 1,440캔들,
   세션 1개, `Venue.BITGET`은 24×7 `CONTINUOUS`라 하루가 정확히 세션
   1개다)로 고정한 리플레이가 0.5s 목표를 실측 통과하는지 확인한다(진짜
   `assert`). CI에서 매번 돈다.

2/3. `test_replay_43200_candles_under_5s`(1개월)·
   `test_replay_525600_candles_under_30s`(1년) — **정직한 실측 노트
   (LA-23b 구현 중 발견)**: `domain.candle_columns`(컬럼지향 읽기)는 레코드
   생성의 pydantic 검증 비용을 실제로 없애지만, `domain/lineage.
   batch_hash`의 지배적 비용은 정렬도 최종 해시 집계도 아니라 **레코드별
   canonical JSON 직렬화**(`_canonical_json`의 `model_dump(mode="json")` +
   `json.dumps`) 그 자체다 — 이 비용은 스트리밍 재구현으로 줄지 않는다
   (같은 직렬화 호출을 그대로 한다, `domain/lineage.py` 모듈 docstring).
   직렬화 자체를 빠르게 하려면 `model_dump_json()` 같은 대안이 필요한데,
   그 출력은 기존 저장 해시와 바이트 동일하지 않아 `hash_version=2` 없이는
   쓸 수 없다(같은 ADR #2 "Rejected") — §8.4 목표(1개월 5s) 실달성은 CA
   ADR 개정 사안으로 백로그에 남긴다(task-1122 decision, 이번 QA 스콥
   제외).

   `test_replay_525600_candles_under_30s`(1년, 525,600봉)는
   `@pytest.mark.nightly`로 분리해 기본 CI 실행(`addopts = -m "not
   nightly"`, pyproject.toml)에서는 돌지 않는다(esc-ci-d6f71c240915: 공유
   CI에서 타임아웃까지 행(hang)한 전례).

   `test_replay_43200_candles_under_5s`(1개월, 43,200봉)는 ADR-2026-09-04-A
   #3 "CI에서 1개월까지 강제"를 충족하기 위해 nightly가 아닌 기본 CI에서
   돈다(task-1122 decision(c)). 다만 이 환경 실측(8.6s)이 §8.4 목표(5s)를
   넘겨 하드 5s 단언은 CI를 상시 적색으로 만든다 — task-1038/3ea1fc1
   (ledger `test_perf_journal` p95 단언 비차단 강등) 선례와 같은 처방으로,
   §8.4 목표(5s)는 문서에 그대로 두되 이 테스트의 차단 게이트는 실측값
   print + 회귀 상한(20.0s, 관측치의 ~2배 여유)만 남긴다 — 목표 완화(임계
   상향)도 xfail 은닉(task-920 XPASS strict 전례)도 아니다.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
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

_ROW_COUNT = 525_600  # 60(분) × 24(시) × 365(일) — 1분봉 1년치, §8.4 "1년"
_MONTH_ROW_COUNT = 43_200  # 60(분) × 24(시) × 30(일) — 1분봉 1개월치, §8.4 "1개월"
_DAY_ROW_COUNT = 1_440  # 60(분) × 24(시) — 1분봉 1일치, §8.4 "1일"
_YEAR_TARGET_SECONDS = 30.0  # §8.4 1년(525,600) ≤ 30s(P95, 단일 노드, ADR-2026-09-04-A)
_MONTH_TARGET_SECONDS = 5.0  # §8.4 1개월(43,200) ≤ 5s(문서 목표, 참고용 — 하드 단언 아님)
_MONTH_REGRESSION_CEILING_SECONDS = 20.0  # 회귀 상한(task-1122 decision(c), 실측 ~8.6s 대비 여유)
_DAY_TARGET_SECONDS = 0.5  # §8.4 1일(1,440) ≤ 0.5s
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


async def _seed_candles(
    pool, batch_repo, *, instrument_id: uuid.UUID, t0: datetime, row_count: int
) -> tuple[IngestBatchResult, datetime]:
    """`row_count`개의 연속 1분봉을 COPY로 적재한다(모듈 docstring —
    파라미터화 INSERT는 연단위 규모에서 바인드 파라미터 상한을 넘긴다).

    `md_candle`은 월별 RANGE 파티션(LA-11)이고 `md_ensure_partitions
    (months_ahead)`(마이그레이션 `4a1d0c0de008`)는 **현재 월부터 미래로만**
    파티션을 만든다 — 과거로 시딩하면 파티션이 없어 INSERT가
    `CheckViolationError: no partition of relation ... found`로 거부된다.
    그래서 `t0`는 항상 현재 시각 이후여야 하고, 시딩 전에 이 함수를 호출해
    `_ROW_COUNT`(약 365일) 규모까지도 덮을 만큼(13개월치) 파티션을 미리
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


@pytest.mark.perf
async def test_replay_1day_1440_candles_under_5s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1일(1,440) ≤ 0.5s 목표를 실측 통과시킨다 — xfail이 아닌 진짜
    `assert`다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    # `Venue.BITGET`은 24×7 `CONTINUOUS`(known_venues.py)라 UTC 자정에 맞춘
    # 하루는 `VenueCalendar.sessions_for`가 정확히 세션 1개를 반환한다
    # (session_rules.py) — "세션 1개" 전제(decision, task-826)를 만족시키기
    # 위해 `t0`를 다음 UTC 자정으로 고정한다. `_seed_candles`가
    # `md_ensure_partitions`로 현재 월부터 미래로만 파티션을 만들 수
    # 있으므로(함수 docstring) t0는 현재 이후여야 하는 제약도 함께 만족한다.
    today = datetime.now(timezone.utc).date()
    t0 = datetime.combine(today, dt_time.min, tzinfo=timezone.utc) + timedelta(days=1)

    _, as_of = await _seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=_DAY_ROW_COUNT
    )

    request = ReplayRequest(
        key=key, start=t0, end=t0 + timedelta(minutes=_DAY_ROW_COUNT), as_of=as_of
    )

    started = time.perf_counter()
    series = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    elapsed_seconds = time.perf_counter() - started

    print(
        f"\nmarket_data replay latency (1day/{_DAY_ROW_COUNT}candles): "
        f"{elapsed_seconds:.3f}s (rows={series.expected_count}, target<{_DAY_TARGET_SECONDS}s)"
    )

    assert series.expected_count == _DAY_ROW_COUNT
    assert series.missing_count == 0
    assert elapsed_seconds < _DAY_TARGET_SECONDS, (
        f"리플레이 {_DAY_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"목표({_DAY_TARGET_SECONDS}s)를 초과했습니다."
    )


@pytest.mark.perf
@pytest.mark.nightly
async def test_replay_525600_candles_under_30s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1년(525,600) ≤ 30s 목표(ADR-2026-09-04-A). 실행 시간이 길고
    (모듈 docstring의 실측 노트 참고 — `batch_hash`의 지배적 비용인
    레코드별 canonical JSON 직렬화는 LA-23b로 줄지 않는다) 기본 CI
    (`pyproject.toml` addopts가 `-m "not nightly"`로 제외)에서는 돌지 않고
    nightly 성능 잡에서만 돈다(esc-ci-d6f71c240915: 이 테스트가 CI 전체를
    타임아웃까지 행(hang)시킨 전례가 있어 상시 실행 대상에서 뺀다) —
    명시적으로 `pytest -m nightly`로 호출해야 실행된다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    # `_seed_candles`가 `md_ensure_partitions`로 현재 월부터 미래로만
    # 파티션을 만들 수 있으므로(함수 docstring), t0는 현재 이후여야 한다.
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)

    _, as_of = await _seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=_ROW_COUNT
    )

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
        f"(rows={series.expected_count}, target<{_YEAR_TARGET_SECONDS}s)"
    )

    assert series.expected_count == _ROW_COUNT
    assert series.missing_count == 0
    assert elapsed_seconds < _YEAR_TARGET_SECONDS, (
        f"리플레이 {_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"목표({_YEAR_TARGET_SECONDS}s)를 초과했습니다."
    )


@pytest.mark.perf
async def test_replay_43200_candles_under_5s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1개월(43,200) ≤ 5s 목표(ADR-2026-09-04-A)를 기본 CI에서 강제
    한다(ADR #3, task-1122 decision(c)). 모듈 docstring의 실측 노트대로
    `batch_hash`의 레코드별 canonical JSON 직렬화 비용이 LA-23b로 줄지
    않아 이 환경 실측(8.6s)은 5s를 넘긴다 — 하드 5s 단언 대신 실측값을
    print하고 회귀 상한(20.0s)만 차단 게이트로 건다(task-1038/3ea1fc1과
    동일 처방). 5s 실달성은 SSOT 변경(hash_version=2 등)이 필요한 CA ADR
    개정 사안으로 이 QA 스콥 밖이다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)

    _, as_of = await _seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=_MONTH_ROW_COUNT
    )

    request = ReplayRequest(
        key=key, start=t0, end=t0 + timedelta(minutes=_MONTH_ROW_COUNT), as_of=as_of
    )

    started = time.perf_counter()
    series = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    elapsed_seconds = time.perf_counter() - started

    print(
        f"\nmarket_data replay latency (1month/{_MONTH_ROW_COUNT}candles): "
        f"{elapsed_seconds:.3f}s (rows={series.expected_count}, target<{_MONTH_TARGET_SECONDS}s)"
    )

    assert series.expected_count == _MONTH_ROW_COUNT
    assert series.missing_count == 0
    # §8.4 목표(5.0s)는 문서 그대로 두되(임계 상향 아님) 이 테스트의 차단
    # 게이트는 회귀 상한(20.0s)만 둔다 — task-1122 decision(c), 선례
    # task-1038/3ea1fc1(ledger test_perf_journal p95 단언 비차단 강등).
    assert elapsed_seconds < _MONTH_REGRESSION_CEILING_SECONDS, (
        f"리플레이 {_MONTH_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"회귀 상한({_MONTH_REGRESSION_CEILING_SECONDS}s)을 초과했습니다."
    )
