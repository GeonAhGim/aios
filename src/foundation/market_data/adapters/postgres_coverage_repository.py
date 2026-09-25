"""DC-8 — asyncpg implementation of `ports/coverage_repository.py`(DC-5).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-8, §4.1(fail-closed), §6(forbid filling gaps outside coverage with 0/NaN),
§9.2 DC-8.

Uses `CoverageSpan`(instrument_id·venue·timeframe·quality·start·end)
defined in `ports/coverage_repository.py` as-is — that module already
specifies this type as the "storage contract" (task-1195 decision:
"Implement DC-5 Protocol without overriding"). The similarly-named type
in `contracts/v2/coverage.py`(DC-6) is not used by this adapter — the
field sets differ(asset_class presence, quality_grade 3-level vs quality
2-level) so they are incompatible without conversion, and that contract
integration is out of scope for this leaf.

Overlap merging (`domain/coverage/registry.merge_spans`) is not called
by this adapter — that function only works with
`contracts/v2/coverage.CoverageSpan`(a different type), and this
leaf's `upsert_span`/`list_spans` perform storage and simple lookup,
not merging (port docstring: "Merging is the responsibility of
domain/coverage/registry.py(DC-6), this module only stores"). Inserting
overlapping spans is rejected by the DB EXCLUDE constraint(DC-8
migration); merging before insert is the caller's responsibility
(upper application layer).
"""
from __future__ import annotations

import asyncpg

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan

__all__ = ["CoverageSpanOverlapError", "PostgresCoverageRepository"]


class CoverageSpanOverlapError(Exception):
    """Raised when `upsert_span()` claims overlapping `[start, end)` spans
    within the same (instrument_id, venue, timeframe, quality) axis —
    violation of the `EXCLUDE USING gist` constraint on `coverage_spans`
    (DC-8). §4.1: inserting overlapping raw declarations must be rejected
    fail-closed; merging must be done first by the caller via
    `domain/coverage/registry.merge_spans`."""


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
