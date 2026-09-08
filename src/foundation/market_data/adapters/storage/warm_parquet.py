"""DC-14 — warm-tier (Parquet, instrument x year) candle storage — round-trips
`CandleColumns` directly.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-14 (depends on DC-13), §9.2 DC-14.

Implements, as-is, the `ports/candle_store.CandleStore` port
(`read_candles_columnar`) delegated to by DC-13's
`hot_postgres.HotPostgresStorage` (decision: no new port or new DTO — §C
duplicate-context rule). The warm tier is an archive of old partitions
promoted from hot (`md_candle`), so it only has append-only batch writes
(owned by DC-15 `tiering.py`) and has no metadata corresponding to hot's
`created_at` snapshot isolation (`as_of`) — silently ignoring `as_of` risks
returning a different (broader) result than hot, so it is rejected
fail-closed (`AsOfNotSupportedError`).

File layout: `<root>/<venue>/<timeframe>/<instrument_id>/<year>.parquet`
(spec §9.2 DC-14 "instrument x year"). All columns are stored as strings
(`Decimal` -> `str`, `AwareDatetime` -> `isoformat()`) — this avoids the
precision/scale and tz normalization that pyarrow's `decimal128`/`timestamp`
types would force, in order to guarantee DoD(a) byte-identical round-tripping
(file size ends up larger than binary encoding would produce — an honest
trade-off left as-is).

Uses pyarrow only (Apache-2.0) — bringing in ArcticDB (BSL 1.1) or a
Timescale column store (Timescale License) is prohibited (decision, note (e)).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

__all__ = [
    "AsOfNotSupportedError",
    "YearMismatchError",
    "WarmParquetStorage",
]

_ARROW_SCHEMA = pa.schema(
    [
        ("ts", pa.string()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("quote_volume", pa.string()),
    ]
)

_Row = tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal | None]


class AsOfNotSupportedError(NotImplementedError):
    """Warm parquet files carry no snapshot-isolation metadata (`created_at`)
    — `as_of != None` is therefore unsupported and rejected fail-closed (so as
    not to silently violate the port contract)."""


class YearMismatchError(ValueError):
    """If `write_year(year=...)` receives `open_time` values from a different
    year mixed in, the filename would lie about the actual contents —
    rejected rather than silently truncated."""


def _series_dir(root: Path, key: SeriesKey) -> Path:
    return root / key.venue.value / key.timeframe.value / str(key.instrument_id)


def _year_path(root: Path, key: SeriesKey, year: int) -> Path:
    return _series_dir(root, key) / f"{year}.parquet"


def _add_months(dt: datetime, n: int) -> datetime:
    total = dt.month - 1 + n
    year = dt.year + total // 12
    month = total % 12 + 1
    return dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _months_between(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Returns the calendar-month windows overlapping `[start, end)`, clipped to month
    boundaries."""
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows: list[tuple[datetime, datetime]] = []
    while cursor < end:
        nxt = _add_months(cursor, 1)
        windows.append((max(cursor, start), min(nxt, end)))
        cursor = nxt
    return windows


def _coalesce_missing(
    ranges: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merges adjacent/overlapping missing ranges into one (compresses the
    raw per-month ranges into a minimal, human-readable list of ranges)."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for seg_start, seg_end in ordered[1:]:
        last_start, last_end = merged[-1]
        if seg_start <= last_end:
            merged[-1] = (last_start, max(last_end, seg_end))
        else:
            merged.append((seg_start, seg_end))
    return merged


def _read_year_file(path: Path) -> list[_Row]:
    """Lets pyarrow's exception propagate as-is for a corrupted (truncated)
    parquet file (DoD(d) — silently swallowing it into an empty result is a
    failure)."""
    table = pq.read_table(path, schema=_ARROW_SCHEMA)
    rows: list[_Row] = []
    for record in table.to_pylist():
        quote_volume = record["quote_volume"]
        rows.append(
            (
                datetime.fromisoformat(record["ts"]),
                Decimal(record["open"]),
                Decimal(record["high"]),
                Decimal(record["low"]),
                Decimal(record["close"]),
                Decimal(record["volume"]),
                None if quote_volume is None else Decimal(quote_volume),
            )
        )
    return rows


class WarmParquetStorage:
    """File-based (Parquet) adapter implementing the column-oriented read
    surface required by `ports/candle_store.CandleStore`. Writing only
    exposes this leaf's round-trip-DoD-verification surface (`write_year`)
    — the actual hot->warm promotion orchestration is owned by DC-15
    `tiering.py` (which uses this class as its callee)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        n = len(columns)
        lengths = {
            "open": len(columns.open),
            "high": len(columns.high),
            "low": len(columns.low),
            "close": len(columns.close),
            "volume": len(columns.volume),
            "quote_volume": len(columns.quote_volume),
        }
        if any(length != n for length in lengths.values()):
            raise MismatchedColumnLengthError({"ts": n, **lengths})
        if any(ts.year != year for ts in columns.ts):
            raise YearMismatchError(
                f"write_year(year={year})에 다른 연도의 open_time이 섞여 있다(fail-closed)."
            )

        table = pa.table(
            {
                "ts": pa.array([ts.isoformat() for ts in columns.ts], type=pa.string()),
                "open": pa.array([str(v) for v in columns.open], type=pa.string()),
                "high": pa.array([str(v) for v in columns.high], type=pa.string()),
                "low": pa.array([str(v) for v in columns.low], type=pa.string()),
                "close": pa.array([str(v) for v in columns.close], type=pa.string()),
                "volume": pa.array([str(v) for v in columns.volume], type=pa.string()),
                "quote_volume": pa.array(
                    [None if v is None else str(v) for v in columns.quote_volume],
                    type=pa.string(),
                ),
            },
            schema=_ARROW_SCHEMA,
        )
        path = _year_path(self._root, key, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        return path

    def read_columns(
        self, key: SeriesKey, start: AwareDatetime, end: AwareDatetime
    ) -> tuple[CandleColumns, tuple[tuple[datetime, datetime], ...]]:
        """Scans `[start, end)` month by month, slicing out and returning
        only the portion covered by year files, and reports the rest via the
        second return value (missing ranges, merged in ascending order)
        (DoD(b) — does not collapse into an empty result)."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end는 tz-aware datetime이어야 한다(fail-closed).")
        if end <= start:
            raise ValueError(f"start >= end: {start!r} >= {end!r}(fail-closed 구간 역전).")

        year_cache: dict[int, list[_Row]] = {}

        def rows_for_year(year: int) -> list[_Row]:
            if year not in year_cache:
                path = _year_path(self._root, key, year)
                year_cache[year] = _read_year_file(path) if path.exists() else []
            return year_cache[year]

        matched: list[_Row] = []
        missing: list[tuple[datetime, datetime]] = []
        for month_start, month_end in _months_between(start, end):
            in_month = [
                row for row in rows_for_year(month_start.year) if month_start <= row[0] < month_end
            ]
            if not in_month:
                missing.append((month_start, month_end))
                continue
            matched.extend(in_month)

        matched.sort(key=lambda row: row[0])
        columns = CandleColumns(
            ts=[row[0] for row in matched],
            open=[row[1] for row in matched],
            high=[row[2] for row in matched],
            low=[row[3] for row in matched],
            close=[row[4] for row in matched],
            volume=[row[5] for row in matched],
            quote_volume=[row[6] for row in matched],
        )
        return columns, tuple(_coalesce_missing(missing))

    async def read_candles_columnar(
        self,
        _conn: object,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> CandleColumns:
        """Same name and shape as `CandleStore.read_candles_columnar`
        (reusing the port DC-13 uses) — unlike the hot tier, no transaction
        is needed, so `_conn` is accepted and ignored. Callers that need
        missing-range information must call `read_columns` directly (the
        port's return type is fixed to `CandleColumns`, so nothing more can
        be attached here)."""
        if as_of is not None:
            raise AsOfNotSupportedError(
                "warm parquet은 스냅샷 메타데이터가 없어 as_of를 지원하지 않는다(fail-closed)."
            )
        columns, _missing = self.read_columns(key, start, end)
        return columns
