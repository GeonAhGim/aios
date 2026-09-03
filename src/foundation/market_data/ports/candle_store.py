"""LA-9 — 캔들/틱/격리 저장·조회 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_candle_store.py,
LA-13)은 모른다(71번 §4). `conn`은 호출자가 이미 연 `asyncpg.Connection`을 그대로
넘긴다는 계약만 표현한다(LC-8a `src/foundation/ledger/ports/*.py`와 같은 패턴).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, QualityIssue, SeriesKey
from src.foundation.market_data.domain.candle_columns import CandleColumns


@runtime_checkable
class CandleStore(Protocol):
    async def upsert_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        """§5 `ON CONFLICT (venue, instrument_id, timeframe, open_time) DO
        NOTHING` — 반환값은 실제로 새로 저장된 행 수(재실행 시 0이어도
        오류 아님)."""
        ...

    async def quarantine(
        self,
        conn: asyncpg.Connection,
        batch_id: UUID,
        candles: list[CandleRecord],
        issues: list[QualityIssue],
    ) -> None:
        """`md_quarantine_candle`에 판정 근거(issues)와 함께 격리 저장 —
        정상 테이블에는 쓰지 않는다."""
        ...

    async def query(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> list[CandleRecord]:
        """`as_of` 이전에 저장된 배치만 조회한다(A5 결정론). `as_of=None`이면
        최신 저장 상태."""
        ...

    async def last_open_time(
        self, conn: asyncpg.Connection, key: SeriesKey
    ) -> AwareDatetime | None:
        """저장된 캔들이 없으면 `None`(스케줄러가 첫 백필 범위 판단에 사용)."""
        ...

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> CandleColumns:
        """LA-23b(ADR-2026-09-04-A #1) — `query()`와 같은 필터(`as_of` 스냅샷
        포함)로 `open_time ASC` 정렬된 컬럼 배열을 돌려준다. 대량 소비자
        (리플레이·`get_candles.load_series`)가 레코드별 pydantic 검증
        없이(`domain/candle_columns.to_candle_records`) 순회하기 위한 내부
        전용 경로 — `query()`를 대체하지 않는다(소량 조회는 여전히
        `query()`가 더 단순하다, 예: positions 컨텍스트의 mark price 조회)."""
        ...
