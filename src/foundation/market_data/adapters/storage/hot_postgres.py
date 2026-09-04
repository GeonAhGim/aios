"""DC-13 — hot 계층(최근 파티션) 캔들 저장: `instrument_id` 키 읽기/쓰기.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-13, §9.2 DC-13(DoD: `instrument_id` 키 조회 p95 200ms, 5,000봉).

이 리프의 decision(task-1212)은 마이그레이션 신설을 금지하고 LA-11
`md_candle`(마이그레이션 `4a1d0c0de008`, task-450 커밋 `c7ff06f`)과 그 파티션
생성 함수 `md_ensure_partitions`를 그대로 재사용하라고 명시한다 — "hot
계층"은 `md_candle`과 물리적으로 다른 테이블이 아니라 그 파티션 중 최근
구간을 가리키는 논리적 이름이다(오래된 파티션을 warm 계층 Parquet로
내보내는 승격·아카이브는 DC-15 `tiering.py` 소관, 이 리프는 손대지 않는다).

`query`/`upsert_batch`/`read_candles_columnar`는 LA-13
`adapters/postgres_candle_store.PostgresCandleStore`가 이미 `md_candle` 위에
구현했고(같은 CHECK·PK·WORM 위에서 동작), 이 어댑터가 그 SQL을 다시 쓰면
DC-13 decision이 금지한 "기능이 겹치면 재구현" 그 자체가 된다 — 그래서
`HotPostgresStorage`는 `PostgresCandleStore`에 위임하는 얇은 파사드다.
이 리프가 실제로 더하는 것은 (1) DC 컨텍스트가 `SeriesKey`를 직접 만들지
않고 `instrument_id`·`venue`·`timeframe`만으로 호출할 수 있는 표면과,
(2) `md_ensure_partitions` 호출(파티션 사전 생성) 래퍼뿐이다.

경계(명세와의 편차, 정직하게 남겨 둔다): 여기서 받는 `instrument_id`는
`md_candle.instrument_id`가 참조하는 `md_instrument`(LA 네임스페이스,
UUID)의 식별자다. DC-8 `coverage_spans`/`instruments`(DC-4, task-1195)는
별도로 `VARCHAR(26)` ULID `instrument_id`를 쓴다 — 두 식별자 공간을
잇는 매핑은 이 리프 범위 밖이다(어느 리프가 그 브리지를 만들지는
needs_decision 대상; DC-16 백필잡이나 DC-9 entitlement 판정이 두 값을
동시에 다뤄야 하는 시점에 결정 필요)."""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["HotPostgresStorage"]


class HotPostgresStorage:
    """`md_candle`(hot 계층) 읽기/쓰기 — `PostgresCandleStore` 위임 파사드.

    `conn`은 호출자가 이미 연 `asyncpg.Connection`을 그대로 받는다(LA-9
    `ports/candle_store.CandleStore`와 동일 계약 — 트랜잭션 경계는 이
    어댑터가 아니라 호출자 소관)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._candle_store = PostgresCandleStore(pool)

    async def read_columns(
        self,
        conn: asyncpg.Connection,
        instrument_id: UUID,
        venue: Venue,
        timeframe: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None = None,
    ) -> CandleColumns:
        """`[start, end)` 구간을 `open_time ASC`로 정렬된 컬럼 배열로
        반환한다(ADR-2026-09-04-A #1 `CandleColumns` 재정의 없이 재사용).
        존재하지 않는 `instrument_id`·빈 구간·미래 구간은 WHERE 절이 그냥
        0행을 돌려주므로 빈 `CandleColumns`다 — 예외를 던지지 않는다(§6
        커버리지 밖 구간을 0/NaN으로 채우지 말라는 불변조건과는 별개 얘기:
        이 메서드는 "커버리지 판정"을 하지 않고 있는 그대로의 저장 상태만
        보고한다. 커버리지 부재 판정·`DATA_COVERAGE_MISSING` 오류는
        `domain/coverage/gaps.py`(DC-7) 소관)."""
        key = SeriesKey(venue=venue, instrument_id=instrument_id, timeframe=timeframe)
        return await self._candle_store.read_candles_columnar(conn, key, start, end, as_of)

    async def write_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        """§5 `ON CONFLICT (venue, instrument_id, timeframe, open_time) DO
        NOTHING`(LA-13 그대로) — 재수집 멱등, 반환값은 실제로 새로 저장된
        행 수."""
        return await self._candle_store.upsert_batch(conn, batch_id, candles)

    async def ensure_partitions(self, conn: asyncpg.Connection, months_ahead: int = 3) -> None:
        """`md_ensure_partitions(months_ahead)`(마이그레이션
        `4a1d0c0de008`, SECURITY DEFINER) 호출 — 새 파티션 DDL을 여기서
        만들지 않는다(decision). 이 함수는 현재 월부터 미래로만 파티션을
        만든다(그 마이그레이션 docstring) — 과거 구간 백필은 대상 파티션이
        이미 존재해야 한다."""
        await conn.execute("SELECT md_ensure_partitions($1)", months_ahead)
