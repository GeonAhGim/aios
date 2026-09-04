"""DC-8 — `ports/coverage_repository.py`(DC-5)의 asyncpg 구현.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-8, §4.1(fail-closed), §6(커버리지 밖 구간 0/NaN 채움 금지),
§9.2 DC-8.

`ports/coverage_repository.py`가 정의한 `CoverageSpan`(instrument_id·
venue·timeframe·quality·start·end)을 그대로 쓴다 — 그 모듈이 이미 이
타입을 "저장 계약"이라고 명시하므로 재정의하지 않는다(task-1195 decision:
"DC-5 Protocol을 재정의 없이 구현"). `contracts/v2/coverage.py`(DC-6)의
동명 타입은 이 어댑터가 쓰지 않는다 — 필드 집합이 달라(asset_class 유무,
quality_grade 3단계 vs quality 2단계) 서로 변환 없이 호환되지 않고, 그
계약 통합은 이 리프 범위 밖이다.

겹침 병합(`domain/coverage/registry.merge_spans`)은 이 어댑터가 호출하지
않는다 — 그 함수는 `contracts/v2/coverage.CoverageSpan`(다른 타입)에서만
동작하고, 이 리프의 `upsert_span`/`list_spans`는 병합이 아니라 저장·단순
조회만 한다(포트 docstring: "병합은 domain/coverage/registry.py(DC-6)
소관, 여기는 저장만 한다"). 겹치는 구간 삽입은 DB EXCLUDE 제약(DC-8
마이그레이션)이 거부하며, 병합해서 넣는 것은 호출자(상위 application
계층)의 책임이다.
"""
from __future__ import annotations

import asyncpg

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan

__all__ = ["CoverageSpanOverlapError", "PostgresCoverageRepository"]


class CoverageSpanOverlapError(Exception):
    """`upsert_span()`이 같은 (instrument_id, venue, timeframe, quality)
    축 안에서 겹치는 `[start, end)` 구간을 주장함 — `coverage_spans`의
    `EXCLUDE USING gist` 제약(DC-8) 위반. §4.1: 겹치는 원본 선언을 그대로
    삽입하는 것은 fail-closed로 거부돼야 한다 — 병합은 호출자가
    `domain/coverage/registry.merge_spans`로 먼저 해야 한다."""


def _row_to_span(row: asyncpg.Record) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=row["instrument_id"],
        venue=Venue(row["venue"]),
        timeframe=Timeframe(row["timeframe"]),
        quality=CoverageQuality(row["quality"]),
        start=row["start_at"],
        end=row["end_at"],
    )


class PostgresCoverageRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_span(self, conn: asyncpg.Connection, span: CoverageSpan) -> CoverageSpan:
        try:
            row = await conn.fetchrow(
                "INSERT INTO coverage_spans "
                "(instrument_id, venue, timeframe, quality, start_at, end_at) "
                "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
                span.instrument_id,
                span.venue.value,
                span.timeframe.value,
                span.quality.value,
                span.start,
                span.end,
            )
        except asyncpg.exceptions.ExclusionViolationError as exc:
            raise CoverageSpanOverlapError(
                f"겹치는 커버리지 구간: instrument_id={span.instrument_id} "
                f"venue={span.venue.value} timeframe={span.timeframe.value} "
                f"quality={span.quality.value}"
            ) from exc
        return _row_to_span(row)

    async def list_spans(
        self, conn: asyncpg.Connection, instrument_id: str, timeframe: Timeframe
    ) -> list[CoverageSpan]:
        rows = await conn.fetch(
            "SELECT * FROM coverage_spans "
            "WHERE instrument_id = $1 AND timeframe = $2 "
            "ORDER BY start_at ASC",
            instrument_id,
            timeframe.value,
        )
        return [_row_to_span(row) for row in rows]
