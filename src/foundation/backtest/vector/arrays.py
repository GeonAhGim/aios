"""BT-15a (1/2) — numpy array view over the `CandleColumns` column-path.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(1/2). Depends on: BT-14 license evaluation (1b53ad92), BT-2~6 fill model (fa3afe4),
LA-23b `CandleColumns` column-path (be1c88b).

BT-16 (grid/walk-forward/Monte Carlo, 1,000 combinations ≤60s) and BT-17 (multi-symbol
sweep) need candles held as arrays to run large batches of combinations quickly with
numpy/numba. This module does not design that array representation from scratch (§C
avoid duplicate context) — it carries LA-23b `CandleColumns`
(ts/open/high/low/close/volume/quote_volume, in this order) straight over into a numpy
dtype. Field names, order, and count must always match the `CandleColumns` definition,
so `from_candle_columns` asserts that fact itself at execution time (reflection
cross-check — if either side adds/removes/reorders a field alone, it fails immediately).

The `Decimal` → `float64` conversion is a deliberate choice to give up precision: this
engine is the speed-first path for BT-16's large-scale sweeps, and the trustworthy final
fill log is still produced by the event engine (`quick_backtest.run_quick_backtest`,
`Decimal`). How closely the two engines agree element-by-element for a single
combination is BT-15b's concern (fills.py + equivalence tests) — this leaf only handles
the column-loading step that precedes it.

`ts` converts a tz-aware `datetime` into an integer count of nanoseconds since UTC
midnight (using only `timedelta`'s integer fields, without going through floating point
— avoiding the precision loss of the float64 mantissa (52 bits, about 4.5e15) at large
epoch values).

Pure module — no I/O.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np

from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

__all__ = ["ArrayDtypeError", "CandleArrays", "from_candle_columns"]

FloatArray = np.ndarray[Any, np.dtype[np.float64]]
TimestampArray = np.ndarray[Any, np.dtype[np.int64]]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_PRICE_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")


class ArrayDtypeError(TypeError):
    """`BT_VECTOR_ARRAY_DTYPE` — rejected fail-closed when a `CandleArrays` field's dtype,
    or its field correspondence with `CandleColumns`, does not match the contract (no
    silent coercion)."""


@dataclass(frozen=True, slots=True)
class CandleArrays:
    """A numpy array view with the same field names and order as `CandleColumns`
    (ts/open/high/low/close/volume/quote_volume). Index `i` corresponds to a single
    candle just the same — only the storage format differs, numpy arrays instead of
    a Python `list[Decimal]`."""

    ts: TimestampArray  # int64, UTC epoch nanoseconds
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: FloatArray
    quote_volume: FloatArray  # None -> NaN

    def __post_init__(self) -> None:
        n = len(self.ts)
        lengths = {name: len(getattr(self, name)) for name in _PRICE_FIELDS}
        if any(length != n for length in lengths.values()):
            raise MismatchedColumnLengthError({"ts": n, **lengths})
        if self.ts.dtype != np.int64:
            raise ArrayDtypeError(f"CandleArrays.ts dtype이 int64가 아니다: {self.ts.dtype}")
        for name in _PRICE_FIELDS:
            dtype = getattr(self, name).dtype
            if dtype != np.float64:
                raise ArrayDtypeError(f"CandleArrays.{name} dtype이 float64가 아니다: {dtype}")

    def __len__(self) -> int:
        return len(self.ts)


def from_candle_columns(columns: CandleColumns) -> CandleArrays:
    """Transfers `columns` into a `CandleArrays`. If `CandleColumns` adds, removes, or
    reorders a field and this module isn't updated to follow (a sign that §C duplicate
    context is happening), this fails immediately here — it cross-checks the two
    dataclasses' field name lists including order."""
    columns_fields = [f.name for f in fields(CandleColumns)]
    arrays_fields = [f.name for f in fields(CandleArrays)]
    if columns_fields != arrays_fields:
        raise ArrayDtypeError(
            "CandleArrays 필드가 CandleColumns와 더 이상 같은 순서·이름이 아니다 "
            f"(CandleColumns={columns_fields}, CandleArrays={arrays_fields}) — "
            "새 캔들 표현이 생겼거나 한쪽만 갱신됐다"
        )
    return CandleArrays(
        ts=_to_epoch_ns(columns.ts),
        open=_to_float64(columns.open),
        high=_to_float64(columns.high),
        low=_to_float64(columns.low),
        close=_to_float64(columns.close),
        volume=_to_float64(columns.volume),
        quote_volume=_to_float64(columns.quote_volume),
    )


def _to_epoch_ns(values: Sequence[datetime]) -> TimestampArray:
    out = np.empty(len(values), dtype=np.int64)
    for i, ts in enumerate(values):
        delta = ts.astimezone(timezone.utc) - _EPOCH
        micros = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        out[i] = micros * 1_000
    return out


def _to_float64(values: Sequence[Decimal | None]) -> FloatArray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)
