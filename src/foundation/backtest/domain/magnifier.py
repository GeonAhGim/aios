"""BT-7 — bar magnifier (expands higher TF bars into lower TF sequence to determine fill order).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-7, §3.4(`magnifier_tf`), §9.5 BT-7 DoD("Deterministic lower-TF fill order").

When multiple orders (e.g., stop/limit) can simultaneously satisfy trigger
conditions within the same higher TF bar, we need to know "in what price
order did the price traverse that bar" to determine which order filled first.
This module produces that price visit sequence
(open→...→close, `Decimal` tuple).

Two paths:
1. If actual lower TF bars (`lower_bars`, `CandleColumns`) exist within the
   higher bar's time window, use them as-is — scan lower bars in time order,
   extract 4 points per bar using the expansion rule below, and splice them.
2. If `magnifier_tf` is `None`, or `lower_bars` is missing/empty (lower-data
   gap), extract just 4 points from the higher bar itself — falling back to
   "bar-level fill" (not an exception, §DoD (4)).

Expansion rule (applied identically to all bars/all paths, deterministic):
- `close >= open` (bullish/doji): `open → low → high → close`
  (conservative assumption: hit the adverse side/low first, then the favorable
  side/high later)
- `close < open` (bearish): `open → high → low → close`
This rule is an internal convention defined by this module ("unverified" — not
a reflection of a specific exchange's actual tick order, but a self-assumed
conservative approximation of fill realism without tick data).

No look-ahead: `lower_bars` must contain only rows within the higher bar's
time window `[open_time, open_time + duration(higher_tf))` — if any row
falls outside that window (including "future" lower bars belonging to the next
higher bar), raise `LookAheadError`. The caller is responsible for slicing
only "lower bars already known at that higher bar timestamp"; this module
only validates that boundary — it has no portfolio/storage access and cannot
query the future itself.

Sort-precondition re-validation: `CandleColumns` does not guarantee sorting
by type alone (it's only a precondition the adapter's `ORDER BY` is
expected to satisfy — see `candle_columns.py`). If a caller violates this
precondition and passes in shuffled `lower_bars`, the time-window check
alone won't catch it (every row can be inside the window and still be out
of order) — splicing them together as-is would silently make the "fill
order" diverge from actual chronological order. For the same reason DC-10's
`timeframe_rollup.rollup` re-validates the same precondition via
`UnsortedCandlesError`, this module also re-validates ascending `ts` order
here and fails closed with `UnsortedLowerBarsError`.

Timeframe lengths use LA-2 (`domain.timeframe.duration`) as-is; do not
re-implement. The lower-bar container reuses the DC-10 rollup output type
(`CandleColumns`) directly — rollup results can be passed through without
transformation.

Pure domain — no I/O/DB/clock access (all timestamps/data come via arguments).
Does not rely on dict/set iteration — inputs and outputs are always ordered
list/tuple, so the same input always produces byte-identical output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.market_data.api import CandleColumns, duration
from src.foundation.market_data.contracts.v1 import Timeframe

__all__ = [
    "HigherBar",
    "LookAheadError",
    "IncompatibleMagnifierTimeframeError",
    "UnsortedLowerBarsError",
    "validate_magnifier_config",
    "magnify",
]


@dataclass(frozen=True, slots=True)
class HigherBar:
    """One higher TF bar to magnify (OHLC + open_time of that bar)."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class LookAheadError(ValueError):
    """`BT_MAGNIFIER_LOOK_AHEAD` — Received lower bars for magnification that
    fall outside the higher bar's time window (including "future" lower bars
    belonging to the next higher bar)."""


class IncompatibleMagnifierTimeframeError(ValueError):
    """`BT_MAGNIFIER_TF_INCOMPATIBLE` — `magnifier_tf` is greater than or
    equal to the higher TF, or cannot divide the higher TF length by an integer."""


class UnsortedLowerBarsError(ValueError):
    """`BT_MAGNIFIER_UNSORTED_LOWER_BARS` — `lower_bars.ts` is not in
    ascending order. Even if every row is inside the higher bar's time
    window (which `LookAheadError` won't catch), an out-of-order sequence
    makes the fill order diverge from actual chronological order."""


def validate_magnifier_config(*, higher_tf: Timeframe, magnifier_tf: Timeframe | None) -> None:
    """Validate whether `magnifier_tf` qualifies to magnify `higher_tf`.

    `magnifier_tf=None` is always valid (no magnification — not an exception,
    §DoD (4))."""

    if magnifier_tf is None:
        return
    higher_step = duration(higher_tf)
    lower_step = duration(magnifier_tf)
    if lower_step >= higher_step:
        raise IncompatibleMagnifierTimeframeError(
            f"magnifier_tf({magnifier_tf!r})는 상위 TF({higher_tf!r})보다 "
            "짧아야 한다"
        )
    if higher_step % lower_step != timedelta(0):
        raise IncompatibleMagnifierTimeframeError(
            f"상위 TF({higher_tf!r})는 magnifier_tf({magnifier_tf!r})의 정수배가 아니다"
        )


def _expand_single_bar(
    open_: Decimal, high: Decimal, low: Decimal, close: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if close >= open_:
        return (open_, low, high, close)
    return (open_, high, low, close)


def magnify(
    higher_bar: HigherBar,
    *,
    higher_tf: Timeframe,
    magnifier_tf: Timeframe | None,
    lower_bars: CandleColumns | None = None,
) -> tuple[Decimal, ...]:
    """Deterministically produce the price visit sequence for one `higher_bar`.

    The same (`higher_bar`, `higher_tf`, `magnifier_tf`, `lower_bars`) input
    always produces the same tuple — no global clock, random, or dict/set
    iteration on any path.
    """

    validate_magnifier_config(higher_tf=higher_tf, magnifier_tf=magnifier_tf)

    if magnifier_tf is None or lower_bars is None or len(lower_bars) == 0:
        return _expand_single_bar(
            higher_bar.open, higher_bar.high, higher_bar.low, higher_bar.close
        )

    for i in range(len(lower_bars) - 1):
        if lower_bars.ts[i] > lower_bars.ts[i + 1]:
            raise UnsortedLowerBarsError(
                f"open_time({lower_bars.ts[i]!r}) at index {i} is later than"
                f"({lower_bars.ts[i + 1]!r}) at index {i + 1}"
            )

    window_start = higher_bar.open_time
    window_end = higher_bar.open_time + duration(higher_tf)

    visited: list[Decimal] = []
    for i in range(len(lower_bars)):
        ts = lower_bars.ts[i]
        if ts < window_start or ts >= window_end:
            raise LookAheadError(
                f"lower bar at index {i}(open_time={ts!r}) is outside the higher"
                f" bar time window [{window_start!r}, {window_end!r})"
            )
        visited.extend(
            _expand_single_bar(
                lower_bars.open[i], lower_bars.high[i], lower_bars.low[i], lower_bars.close[i]
            )
        )
    return tuple(visited)
