"""LA-13 — asyncpg implementation of `CandleStore` (ports/candle_store.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-13.

The PK of `md_candle` (LA-11, migration 4a1d0c0de008) is already
`(venue, instrument_id, timeframe, open_time)`, so attaching
`ON CONFLICT DO NOTHING` on that PK directly makes re-collection idempotent —
no additional UNIQUE constraint needs to be located. The 6 CHECK constraints
(OHLC inequalities) are enforced by the DB, so this adapter does not wrap
`asyncpg.exceptions.CheckViolationError` (candles that bypassed code validation
must also be rejected here, as the migration docstring states).

Snapshot isolation for `query(as_of=...)` is implemented via
`md_candle.created_at` (WORM, never updated afterwards) — batches arriving
after `as_of` have `created_at > as_of` and are automatically filtered out.
"""
from __future__ import annotations

from typing import cast
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    SeriesKey,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["PostgresCandleStore"]

_COLUMNAR_FIELDS = ("open_time", "open", "high", "low", "close", "volume", "quote_volume")

_CANDLE_COLUMNS = (
    "venue",
    "instrument_id",
    "timeframe",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "batch_id",
)


def _row_to_candle(row: asyncpg.Record) -> CandleRecord:
    return CandleRecord(
        key=SeriesKey(
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            timeframe=Timeframe(row["timeframe"]),
        ),
        open_time=row["open_time"],
        close_time=row["close_time"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        quote_volume=row["quote_volume"],
    )


def _candle_params(candle: CandleRecord, batch_id: UUID) -> tuple[object, ...]:
    return (
        candle.key.venue.value,
        candle.key.instrument_id,
        candle.key.timeframe.value,
        candle.open_time,
        candle.close_time,
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.quote_volume,
        batch_id,
    )


def _issue_type_for(open_time: AwareDatetime, issues: list[QualityIssue]) -> QualityIssueType:
    """`md_quarantine_candle.issue_type` is singular per candle row, but
    `issues` received by the port is the evidence list for the whole batch —
    find issues pointing to the same `open_time` first; if none exist (issues
    recorded at batch level only), use the first issue as a representative.
    The full evidence (`detail` included) is left separately in
    `md_quality_issue` by `BatchRepository.add_issues`, so this function
    only serves as a classification tag."""
    for issue in issues:
        if issue.open_time == open_time:
            return issue.type
    if issues:
        return issues[0].type
    raise ValueError("quarantine()은 최소 1개의 QualityIssue가 필요합니다")


class PostgresCandleStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        if not candles:
            return 0

        params: list[object] = []
        value_groups: list[str] = []
        for candle in candles:
            row_params = _candle_params(candle, batch_id)
            offset = len(params)
            placeholders = ", ".join(f"${offset + i + 1}" for i in range(len(row_params)))
            value_groups.append(f"({placeholders})")
            params.extend(row_params)

        rows = await conn.fetch(
            f"INSERT INTO md_candle ({', '.join(_CANDLE_COLUMNS)}) "  # noqa: S608
            f"VALUES {', '.join(value_groups)} "
            "ON CONFLICT (venue, instrument_id, timeframe, open_time) DO NOTHING "
            "RETURNING open_time",
            *params,
        )
        return len(rows)

    async def quarantine(
        self,
        conn: asyncpg.Connection,
        batch_id: UUID,
        candles: list[CandleRecord],
        issues: list[QualityIssue],
    ) -> None:
        if not candles:
            return

        params: list[object] = []
        value_groups: list[str] = []
        columns = (*_CANDLE_COLUMNS, "issue_type")
        for candle in candles:
            row_params = (
                *_candle_params(candle, batch_id),
                _issue_type_for(candle.open_time, issues).value,
            )
            offset = len(params)
            placeholders = ", ".join(f"${offset + i + 1}" for i in range(len(row_params)))
            value_groups.append(f"({placeholders})")
            params.extend(row_params)

        await conn.execute(
            f"INSERT INTO md_quarantine_candle ({', '.join(columns)}) "  # noqa: S608
            f"VALUES {', '.join(value_groups)} "
            "ON CONFLICT (venue, instrument_id, timeframe, open_time, batch_id) DO NOTHING",
            *params,
        )

    async def query(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> list[CandleRecord]:
        rows = await conn.fetch(
            "SELECT * FROM md_candle "
            "WHERE venue = $1 AND instrument_id = $2 AND timeframe = $3 "
            "AND open_time >= $4 AND open_time < $5 "
            "AND ($6::timestamptz IS NULL OR created_at <= $6) "
            "ORDER BY open_time ASC",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
            start,
            end,
            as_of,
        )
        return [_row_to_candle(row) for row in rows]

    async def last_open_time(
        self, conn: asyncpg.Connection, key: SeriesKey
    ) -> AwareDatetime | None:
        value = await conn.fetchval(
            "SELECT MAX(open_time) FROM md_candle "
            "WHERE venue = $1 AND instrument_id = $2 AND timeframe = $3",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
        )
        return cast("AwareDatetime | None", value)

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> CandleColumns:
        rows = await conn.fetch(
            f"SELECT {', '.join(_COLUMNAR_FIELDS)} FROM md_candle "  # noqa: S608
            "WHERE venue = $1 AND instrument_id = $2 AND timeframe = $3 "
            "AND open_time >= $4 AND open_time < $5 "
            "AND ($6::timestamptz IS NULL OR created_at <= $6) "
            "ORDER BY open_time ASC",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
            start,
            end,
            as_of,
        )
        return CandleColumns(
            ts=[row["open_time"] for row in rows],
            open=[row["open"] for row in rows],
            high=[row["high"] for row in rows],
            low=[row["low"] for row in rows],
            close=[row["close"] for row in rows],
            volume=[row["volume"] for row in rows],
            quote_volume=[row["quote_volume"] for row in rows],
        )
