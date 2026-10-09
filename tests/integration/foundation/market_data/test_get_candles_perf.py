"""LA-17 `get_candles` 수치 성능 단언(D3) — 순차 DB 왕복 수(구조 회귀 가드).

`tests/integration/foundation/market_data/test_perf_replay.py`(task-1081/
LA-23b)와 같은 근거·같은 기법이다: RAW 조회는 `CandleStore.last_open_time`
(시계열 존재 확인) + `read_candles_columnar`(컬럼지향 읽기) 2회로 고정이며,
행 수와 무관한 구조 상수다(`Venue.BITGET`은 CONTINUOUS라 세션 계산에 DB
조회가 없다). 그 파일은 `replay()`만 재던 이 상수를 `get_candles()`에는
아직 증명하지 않았다 — task-10929는 `test_get_candles.py`(564줄,
loc_over_500)를 분할하면서 이 책임을 별도 파일로 둔다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.application.get_candles import get_candles
from src.foundation.market_data.contracts.v1 import CandleQuery, SeriesKey
from tests.integration.foundation.market_data.get_candles_support import utc_minute_now
from tests.integration.foundation.market_data.perf_replay_support import (
    DAY_ROW_COUNT,
    new_instrument_id,
    seed_candles,
    series_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_MAX_GET_CANDLES_ROUND_TRIPS = 2


class _PinnedConnectionPool:
    """`get_candles(pool=...)`에 넘기는 풀 대역 — `acquire()`가 항상 미리
    얻어 둔 커넥션 하나를 돌려준다(반납하지 않는다). 워밍업과 계수가 같은
    커넥션에서 일어나야 asyncpg의 1회성 코덱 조회가 계수에 섞이지 않는다
    (`perf_replay_support.py`의 `_PinnedConnectionPool`과 동일 이유 — 그
    모듈은 이 클래스를 공개하지 않으므로 여기서 별도로 둔다)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


async def _count_get_candles_round_trips(
    pool: asyncpg.Pool, query: CandleQuery, *, store, refs, cal
) -> int:
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        pinned = _PinnedConnectionPool(conn)
        await get_candles(query, store=store, refs=refs, cal=cal, pool=pinned)

        conn.add_query_logger(_log)
        try:
            await get_candles(query, store=store, refs=refs, cal=cal, pool=pinned)
        finally:
            conn.remove_query_logger(_log)

    return len(queries)


@pytest.mark.perf
async def test_get_candles_round_trip_count_bounded(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """수치 성능 단언: RAW 조회 1회가 내는 순차 DB 왕복 수가 상한(2)을
    넘지 않는지 1일(1,440행) 규모로 증명한다. 절대시간은 공유 CI 환경에서
    통제할 수 없는 신호라 게이트로 쓰지 않는다(print만, task-1405/
    test_perf_replay.py 선례와 동일 원칙)."""
    async with pool.acquire() as conn:
        instrument_id = await new_instrument_id(conn)
    t0 = utc_minute_now() + timedelta(minutes=1)
    await seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=DAY_ROW_COUNT
    )

    query = CandleQuery(
        key=series_key(instrument_id), start=t0, end=t0 + timedelta(minutes=DAY_ROW_COUNT)
    )
    round_trip_count = await _count_get_candles_round_trips(
        pool, query, store=candle_store, refs=reference_repo, cal=calendar_repo
    )
    print(
        f"\nmarket_data get_candles sequential DB round trips={round_trip_count} "
        f"(max={_MAX_GET_CANDLES_ROUND_TRIPS}, rows={DAY_ROW_COUNT})"
    )
    assert round_trip_count <= _MAX_GET_CANDLES_ROUND_TRIPS, (
        f"get_candles() 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_GET_CANDLES_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


class _ChattyCandleStore(PostgresCandleStore):
    """게이트 적색 재현/실패주입 — 시계열 존재 확인 전에 불필요한 왕복을
    하나 더 낸다(행별 쿼리를 끼워 넣는 회귀의 최소 재현, test_perf_replay.py
    `_ChattyCandleStore`와 동일 기법)."""

    async def last_open_time(self, conn: asyncpg.Connection, key: SeriesKey):
        await conn.fetchval("SELECT 1")
        return await super().last_open_time(conn, key)


@pytest.mark.perf
async def test_get_candles_round_trip_gate_detects_extra_query(
    pool, batch_repo, reference_repo, calendar_repo
):
    """게이트 적색 재현(D3): 왕복을 하나 더 내는 저장소를 끼우면 위 상한(2)
    게이트가 실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라
    "작동함이 증명됨"이어야 한다)."""
    async with pool.acquire() as conn:
        instrument_id = await new_instrument_id(conn)
    t0 = utc_minute_now() + timedelta(minutes=1)
    await seed_candles(pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=10)

    query = CandleQuery(key=series_key(instrument_id), start=t0, end=t0 + timedelta(minutes=10))
    round_trip_count = await _count_get_candles_round_trips(
        pool, query, store=_ChattyCandleStore(pool), refs=reference_repo, cal=calendar_repo
    )
    assert round_trip_count == _MAX_GET_CANDLES_ROUND_TRIPS + 1
    assert round_trip_count > _MAX_GET_CANDLES_ROUND_TRIPS
