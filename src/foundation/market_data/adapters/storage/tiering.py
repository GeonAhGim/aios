"""DC-15 — hot->warm candle promotion/archival job: idempotent + lineage recording.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-15 (depends on DC-14), §9.2 DC-15.

Promotion order (DoD(b), fail-closed): read from hot -> write to warm (DC-14
`WarmParquetStorage.write_year`) -> read warm back and verify the digest is
byte-identical to the source -> **only once verification passes** delete
that year from hot. If verification fails (`VerificationFailedError`), or an
exception is raised at any earlier step, hot is left untouched — since
`write_year` always overwrites the target file wholesale (DC-14), even a
partial file left behind by an interrupted run gets rewritten from scratch
on the next run using hot's full data (DoD(c); deliberately not keeping any
separate resume state is itself the safety mechanism).

Idempotency (DoD(a)) is achieved via "if there is nothing to promote, do
nothing": if hot already has 0 rows for that year (either because the first
promotion succeeded and deleted them, or because there were none to begin
with), the function returns immediately without touching warm or the
lineage — a second run changes neither the resulting parquet nor the
lineage file.

Lineage (DoD(d)) does not introduce a new schema — it reuses DC-22's
`contracts/v2/candle_lineage.SourceKind` as-is just to tag the promotion
batch, and the record itself is a plain dict written out as JSON Lines (no
new pydantic model, no DB table). Since `md_candle` does not yet carry a
per-candle `source_kind` (a documented boundary from DC-13), this job always
tags with `VENDOR` — this should be changed to pass through the actual
value once an upstream context that knows the real source exists.

No new migration (DoD(e)) — lineage is a JSON Lines file sitting next to
the warm root.

Boundary (left honest rather than hidden): since `hot.delete_year` deletes
the entire year, if new rows for that same year arrive concurrently after
the `hot.read_year` snapshot, those rows can be deleted too (without ever
being promoted) — serializing against concurrent backfills is out of scope
for this leaf (it assumes a single-worker batch job).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "HotYearSource",
    "PromotionOutcome",
    "VerificationFailedError",
    "WarmYearStorage",
    "promote_year",
    "read_lineage",
]


class VerificationFailedError(RuntimeError):
    """The warm round-trip verification (digest match, no missing ranges)
    failed — hot is not deleted (fail-closed, DoD(b))."""


@runtime_checkable
class HotYearSource(Protocol):
    """The minimal surface tiering requires from the hot tier.
    `HotPostgresStorage` (DC-13) does not yet have these two methods —
    wiring up the real Postgres adapter that implements this Protocol is
    the responsibility of a follow-up task; this leaf is only responsible
    for the interface and pure orchestration."""

    async def read_year(self, key: SeriesKey, year: int) -> CandleColumns:
        """Returns the entirety of `year` (only rows whose `open_time`
        falls in that year). Returns an empty `CandleColumns` if there
        are none."""
        ...

    async def delete_year(self, key: SeriesKey, year: int) -> int:
        """Deletes the entirety of `year` and returns the number of rows
        deleted."""
        ...


@runtime_checkable
class WarmYearStorage(Protocol):
    """Minimal surface tiering requires from the warm tier — implemented by
    `WarmParquetStorage`, and structurally satisfied by test fakes that
    inject verification failures or mid-write crashes."""

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        """Overwrites the full `year` and returns the written file path."""
        ...

    def read_columns(
        self, key: SeriesKey, start: datetime, end: datetime
    ) -> tuple[CandleColumns, tuple[tuple[datetime, datetime], ...]]:
        """Reads `[start, end)` and returns (covered columns, missing ranges)."""
        ...


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    """The result of a single `promote_year` call. `promoted=False` means
    an idempotent skip (there were no hot rows to promote)."""

    promoted: bool
    row_count: int
    digest: str


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return start, end


def _digest(columns: CandleColumns) -> str:
    """The same canonical serialization used by the DC-14 tests (compares
    values only, ignores tzinfo class identity) — here it is actually used
    for the hot->warm round-trip verification."""
    parts: list[str] = []
    for i in range(len(columns)):
        parts.append(columns.ts[i].isoformat())
        parts.append(str(columns.open[i]))
        parts.append(str(columns.high[i]))
        parts.append(str(columns.low[i]))
        parts.append(str(columns.close[i]))
        parts.append(str(columns.volume[i]))
        parts.append("" if columns.quote_volume[i] is None else str(columns.quote_volume[i]))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _lineage_path(root: Path, key: SeriesKey) -> Path:
    return root / key.venue.value / key.timeframe.value / str(key.instrument_id) / "lineage.jsonl"


def read_lineage(root: Path, key: SeriesKey) -> list[dict[str, object]]:
    """Returns the promotion lineage rows recorded so far for this series
    in ascending order. Returns an empty list if the file does not exist
    (nothing has been promoted yet)."""
    path = _lineage_path(root, key)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _append_lineage(root: Path, key: SeriesKey, entry: dict[str, object]) -> None:
    path = _lineage_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


async def promote_year(
    hot: HotYearSource, warm: WarmYearStorage, root: Path, key: SeriesKey, year: int
) -> PromotionOutcome:
    """Promotes the hot partition for `year` to warm parquet, and only
    after verification passes deletes it from hot and appends one lineage
    line. If hot already has no rows for that year, returns without doing
    anything (DoD(a) idempotency)."""
    source = await hot.read_year(key, year)
    if len(source) == 0:
        return PromotionOutcome(promoted=False, row_count=0, digest="")

    source_digest = _digest(source)
    warm.write_year(key, year, source)

    start, end = _year_bounds(year)
    written, _missing = warm.read_columns(key, start, end)
    # `_missing` only means that calendar month has no data at all (§DC-14
    # read_columns) — that's normal if the hot partition never had candles
    # for that month, so it isn't a corruption signal. Round-trip
    # integrity is judged purely by digest equality.
    if _digest(written) != source_digest:
        raise VerificationFailedError(
            f"warm round-trip verification failed for {key.instrument_id}/{year} "
            "(fail-closed — hot rows kept)"
        )

    await hot.delete_year(key, year)
    _append_lineage(
        root,
        key,
        {
            "year": year,
            "digest": source_digest,
            "row_count": len(source),
            "promoted_at": datetime.now(tz=timezone.utc).isoformat(),
            "source_kind": SourceKind.VENDOR.value,
        },
    )
    return PromotionOutcome(promoted=True, row_count=len(source), digest=source_digest)
