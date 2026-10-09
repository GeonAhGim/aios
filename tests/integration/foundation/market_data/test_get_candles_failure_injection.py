"""LA-17 `get_candles` 실패주입(D3) — 저장소가 손상된 컬럼을 돌려주는 경우.

task-10929: `test_get_candles.py`(564줄, loc_over_500) 분할 산출물 — 핵심
계약 테스트는 그 파일에, 수치 성능 단언은 `test_get_candles_perf.py`에 있다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.application.get_candles import get_candles
from src.foundation.market_data.contracts.v1 import CandleQuery, SeriesKey
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)
from tests.integration.foundation.market_data.get_candles_support import (
    instrument_id as _instrument_id,
)
from tests.integration.foundation.market_data.get_candles_support import (
    seed_candle,
    utc_minute_now,
)


class _CorruptedColumnsCandleStore(PostgresCandleStore):
    """`read_candles_columnar`(LA-23b)가 배열 길이가 서로 다른 `CandleColumns`를
    돌려주는 실제 어댑터 결함을 시뮬레이션한다(예: 컬럼 하나를 별도 조회로
    바꾸다 WHERE 필터가 어긋나는 리팩터링 회귀). `to_candle_records`(domain/
    candle_columns.py)는 인덱스로만 각 배열을 짝짓기 때문에, 길이가 다르면
    조용히 잘못 정렬된 OHLCV를 만들어낼 수 있다 — `get_candles`가 이 결함을
    삼키지 않고 그대로 전파해 fail-closed 하는지 증명한다."""

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: datetime,
        end: datetime,
        as_of: datetime | None,
    ) -> CandleColumns:
        columns = await super().read_candles_columnar(conn, key, start, end, as_of)
        return CandleColumns(
            ts=columns.ts,
            open=columns.open[:-1],
            high=columns.high,
            low=columns.low,
            close=columns.close,
            volume=columns.volume,
            quote_volume=columns.quote_volume,
        )


async def test_get_candles_raises_when_store_returns_mismatched_columns(
    pool, batch_repo, reference_repo, calendar_repo
):
    """실패주입: 저장소가 배열 길이가 어긋난 컬럼을 돌려주면 `get_candles`는
    잘못 정렬된 캔들을 조용히 만들지 않고 `MismatchedColumnLengthError`를
    그대로 전파해야 한다(fail-closed)."""
    corrupted_store = _CorruptedColumnsCandleStore(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = utc_minute_now()
        key = await seed_candle(
            conn, batch_repo, corrupted_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    with pytest.raises(MismatchedColumnLengthError):
        await get_candles(
            query, store=corrupted_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )
