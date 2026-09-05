"""LA-23/LA-23b — 리플레이 §8.4 규모별 **절대시간 계약**(nightly 전용, 실 DB).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9.2 LA-23,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md.

**규모별 계약(ADR-2026-09-04-A §8.4, 단일 노드 P95)**: 1일(1,440) ≤ 0.5s,
1개월(43,200) ≤ 5s, 1년(525,600) ≤ 30s. 이 파일의 테스트는 전부
`@pytest.mark.nightly`라 기본 CI 실행(`addopts = -m "not nightly"`,
pyproject.toml)에서는 돌지 않고, `pytest -m nightly` 또는
`--override-ini addopts=`로 명시 호출해야 실행된다(esc-ci-d6f71c240915:
공유 CI에서 1년 규모가 타임아웃까지 행(hang)한 전례). 기본 CI의 구조
게이트(왕복 수·정합성)는 `test_perf_replay.py`가 담당한다(task-1405 —
절대시간 단언을 부하 편차가 없는 nightly로 분리한 경위는 그 파일 모듈
docstring).

**정직한 실측 노트(LA-23b 구현 중 발견, 그대로 유지)**: `domain.
candle_columns`(컬럼지향 읽기)는 레코드 생성의 pydantic 검증 비용을 실제로
없애지만, `domain/lineage.batch_hash`의 지배적 비용은 정렬도 최종 해시
집계도 아니라 **레코드별 canonical JSON 직렬화**(`_canonical_json`의
`model_dump(mode="json")` + `json.dumps`) 그 자체다 — 이 비용은 스트리밍
재구현으로 줄지 않는다(`domain/lineage.py` 모듈 docstring). 직렬화 자체를
빠르게 하려면 `model_dump_json()` 같은 대안이 필요한데, 그 출력은 기존
저장 해시와 바이트 동일하지 않아 `hash_version=2` 없이는 쓸 수 없다(같은
ADR #2 "Rejected") — §8.4 목표(1개월 5s) 실달성은 CA ADR 개정 사안으로
백로그에 남긴다(task-1122 decision).

그래서 1개월 테스트의 차단 게이트는 task-1122 decision(c)·task-1038/
3ea1fc1 선례대로 5s 하드 단언이 아니라 **회귀 상한 20.0s**(이 환경 실측
8.6~11.3s의 ~2배 여유)이고, 5s 목표는 print로 남긴다 — 목표 완화(임계
상향)도 xfail 은닉(task-920 XPASS strict 전례)도 아니다. task-1405는 이
게이트를 값 변경 없이 기본 CI에서 이 파일로 옮기기만 했다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time

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
from src.foundation.market_data.contracts.v1 import ReplayRequest
from tests.integration.foundation.market_data.perf_replay_support import (
    DAY_ROW_COUNT,
    MONTH_ROW_COUNT,
    YEAR_ROW_COUNT,
    new_instrument_id,
    seed_candles,
    series_key,
)

_DAY_TARGET_SECONDS = 0.5  # §8.4 1일(1,440) ≤ 0.5s
_MONTH_TARGET_SECONDS = 5.0  # §8.4 1개월(43,200) ≤ 5s(문서 목표, 참고용 — 하드 단언 아님)
_MONTH_REGRESSION_CEILING_SECONDS = 20.0  # 회귀 상한(task-1122 decision(c), 실측 대비 여유)
_YEAR_TARGET_SECONDS = 30.0  # §8.4 1년(525,600) ≤ 30s(P95, 단일 노드, ADR-2026-09-04-A)


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


async def _seeded_request(pool, batch_repo, *, t0: datetime, row_count: int) -> ReplayRequest:
    async with pool.acquire() as conn:
        instrument_id = await new_instrument_id(conn)
    _, as_of = await seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=row_count
    )
    return ReplayRequest(
        key=series_key(instrument_id), start=t0, end=t0 + timedelta(minutes=row_count),
        as_of=as_of,
    )


async def _timed_replay(
    pool, request: ReplayRequest, *, label: str, row_count: int, target_seconds: float,
    candle_store, reference_repo, calendar_repo,
) -> float:
    started = time.perf_counter()
    series = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    elapsed_seconds = time.perf_counter() - started
    print(
        f"\nmarket_data replay latency ({label}/{row_count}candles): "
        f"{elapsed_seconds:.3f}s (rows={series.expected_count}, target<{target_seconds}s)"
    )
    assert series.expected_count == row_count
    assert series.missing_count == 0
    return elapsed_seconds


def _next_utc_midnight() -> datetime:
    """`Venue.BITGET`은 24×7 CONTINUOUS라 UTC 자정 기준 하루 = 세션 정확히
    1개(task-826 decision "세션 1개" 전제). 파티션 제약(현재 이후)도 만족."""
    today = datetime.now(timezone.utc).date()
    return datetime.combine(today, dt_time.min, tzinfo=timezone.utc) + timedelta(days=1)


def _next_minute() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)


@pytest.mark.perf
@pytest.mark.nightly
async def test_replay_1day_1440_candles_under_0_5s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1일(1,440) ≤ 0.5s — xfail이 아닌 진짜 `assert`(nightly)."""
    request = await _seeded_request(
        pool, batch_repo, t0=_next_utc_midnight(), row_count=DAY_ROW_COUNT
    )
    elapsed_seconds = await _timed_replay(
        pool, request, label="1day", row_count=DAY_ROW_COUNT,
        target_seconds=_DAY_TARGET_SECONDS, candle_store=candle_store,
        reference_repo=reference_repo, calendar_repo=calendar_repo,
    )
    assert elapsed_seconds < _DAY_TARGET_SECONDS, (
        f"리플레이 {DAY_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"목표({_DAY_TARGET_SECONDS}s)를 초과했습니다."
    )


@pytest.mark.perf
@pytest.mark.nightly
async def test_replay_43200_candles_under_5s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1개월(43,200) ≤ 5s 목표는 print, 차단 게이트는 회귀 상한 20.0s
    (모듈 docstring — task-1122 decision(c), 값 변경 없이 nightly로 이동)."""
    request = await _seeded_request(pool, batch_repo, t0=_next_minute(), row_count=MONTH_ROW_COUNT)
    elapsed_seconds = await _timed_replay(
        pool, request, label="1month", row_count=MONTH_ROW_COUNT,
        target_seconds=_MONTH_TARGET_SECONDS, candle_store=candle_store,
        reference_repo=reference_repo, calendar_repo=calendar_repo,
    )
    assert elapsed_seconds < _MONTH_REGRESSION_CEILING_SECONDS, (
        f"리플레이 {MONTH_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"회귀 상한({_MONTH_REGRESSION_CEILING_SECONDS}s)을 초과했습니다."
    )


@pytest.mark.perf
@pytest.mark.nightly
async def test_replay_525600_candles_under_30s(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """§8.4 1년(525,600) ≤ 30s 목표(ADR-2026-09-04-A). 실행 시간이 길어
    (모듈 docstring의 실측 노트) nightly 성능 잡에서만 돈다."""
    request = await _seeded_request(pool, batch_repo, t0=_next_minute(), row_count=YEAR_ROW_COUNT)
    elapsed_seconds = await _timed_replay(
        pool, request, label="1year", row_count=YEAR_ROW_COUNT,
        target_seconds=_YEAR_TARGET_SECONDS, candle_store=candle_store,
        reference_repo=reference_repo, calendar_repo=calendar_repo,
    )
    assert elapsed_seconds < _YEAR_TARGET_SECONDS, (
        f"리플레이 {YEAR_ROW_COUNT}행 처리 시간({elapsed_seconds:.3f}s)이 "
        f"목표({_YEAR_TARGET_SECONDS}s)를 초과했습니다."
    )
