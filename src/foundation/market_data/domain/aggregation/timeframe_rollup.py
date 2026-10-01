"""DC-10 — Deterministic aggregation of M1 into derived timeframes.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-10, §4.1, §9.2 DC-10.

Takes only M1 candle columns (ADR-A `CandleColumns`, do not redefine) as
input and aggregates them into a higher timeframe. Timeframe grid
alignment (`align_open`/`expected_opens`) and session resolution
(`VenueCalendar`) are delegated to LA-2/LA-3 and not reimplemented here
(LA-19 delegation principle). Pure functions only — no I/O, no asyncpg.

fail-closed rules (§4.1):
- Derived TFs are only produced from M1 — rejected if the target
  timeframe is M1 itself.
- Out-of-coverage windows are never filled with 0/NaN — if a candle is
  expected within the session but the window has zero source M1 rows
  (a gap), that derived candle is simply not produced (no fake
  zero-filled candle).
- `quote_volume` is left `None` for the sum if any M1 row in the window
  has a missing (`None`) value — summing an unknown value as 0 would
  violate §4.1.
- An output without `rollup_version` cannot be produced — `RollupResult`
  always carries it alongside, enforced at the type level.

`rollup_version` is a component of the BT-9 reproducibility key
(`reproducibility_key`). If it were a constant string that a human had to
bump by hand, it would be easy to forget to bump the version when the
aggregation rule changes — so this module instead defines the version as
a hash of the `_ROLLUP_RULE_SPEC` string that describes the aggregation
rule: whenever the rule description changes (i.e. when the aggregation
logic in this module changes along with its description), the value
changes automatically. The limitation — honestly left as-is — is that if
only the code logic changes while `_ROLLUP_RULE_SPEC` is left untouched,
the version will not change; this relies on review discipline to update
the description whenever the rule changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)
from src.foundation.market_data.domain.timeframe import duration, expected_opens

__all__ = [
    "RollupResult",
    "InvalidRollupTargetError",
    "UnsortedCandlesError",
    "SessionNotFoundError",
    "ROLLUP_VERSION",
    "rollup",
]

_ROLLUP_RULE_SPEC = (
    "open=first_m1_open;high=max_m1_high;low=min_m1_low;close=last_m1_close;"
    "volume=sum_m1_volume;quote_volume=sum_or_none_if_any_missing;"
    "grid=epoch_aligned_utc(D1=utc_midnight_via_align_open);"
    "session_boundary=clip_window_end_to_session_close_at;"
    "gap_policy=skip_window_with_zero_source_m1_rows;"
    "source_timeframe=M1_only"
)
ROLLUP_VERSION = f"tfr1-{hashlib.sha256(_ROLLUP_RULE_SPEC.encode('utf-8')).hexdigest()[:16]}"


class InvalidRollupTargetError(ValueError):
    """`MD_ROLLUP_TARGET_INVALID` — M1 cannot be a rollup target (§4.1:
    derived TFs are only produced from M1, so a "rollup" of M1 into
    itself is undefined)."""

    def __init__(self, tf: Timeframe) -> None:
        super().__init__(f"M1은 롤업 대상 timeframe이 될 수 없습니다: {tf!r}")


class UnsortedCandlesError(ValueError):
    """`MD_ROLLUP_UNSORTED_INPUT` — unsorted M1 input would silently pair
    the two-pointer aggregation with the wrong window (rejected
    fail-closed)."""


class SessionNotFoundError(ValueError):
    """`MD_ROLLUP_SESSION_NOT_FOUND` — an open returned by `expected_opens`
    cannot be found again in the session list that produced it (an
    internal invariant violation that should never happen, but is
    rejected defensively)."""


@dataclass(frozen=True, slots=True)
class RollupResult:
    """The output of a single `rollup` call. Always returned paired with
    `rollup_version` so `columns` can never be taken out alone (§4.1:
    storing a derived candle without rollup_version is forbidden —
    enforced at the type level before storage)."""

    columns: CandleColumns
    rollup_version: str


def _sessions_between(
    calendar: VenueCalendar, start_ts: datetime, end_ts: datetime
) -> list[SessionWindow]:
    day = calendar.trading_day_of(start_ts)
    end_day = calendar.trading_day_of(end_ts)
    sessions: list[SessionWindow] = []
    while day <= end_day:
        sessions.extend(calendar.sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _session_containing(ts_open: datetime, sessions: list[SessionWindow]) -> SessionWindow:
    for session in sessions:
        if session.open_at <= ts_open < session.close_at:
            return session
    raise SessionNotFoundError(f"open_time={ts_open!r}에 대응하는 세션을 찾을 수 없습니다")


def rollup(columns: CandleColumns, tf: Timeframe, calendar: VenueCalendar) -> RollupResult:
    """Aggregates `columns` (M1 source, ascending `open_time`) into `tf` candles.

    Given the same inputs (`columns`, `tf`, `calendar`), always produces a
    byte-identical output — every value that determines sort order or
    session windows is taken only from arguments, never from the system
    clock or randomness.
    """
    if tf is Timeframe.M1:
        raise InvalidRollupTargetError(tf)

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
    step = duration(tf)  # validates tf is registered (fail-closed)
    if n == 0:
        return RollupResult(columns=_empty_columns(), rollup_version=ROLLUP_VERSION)
    for i in range(n - 1):
        if columns.ts[i] > columns.ts[i + 1]:
            raise UnsortedCandlesError(
                f"index {i}의 open_time({columns.ts[i]!r})이 index {i + 1}"
                f"({columns.ts[i + 1]!r})보다 늦습니다"
            )

    m1_step = duration(Timeframe.M1)
    range_start = columns.ts[0]
    range_end = columns.ts[-1] + m1_step
    sessions = _sessions_between(calendar, range_start, range_end)
    opens = expected_opens(range_start, range_end, tf, sessions)

    out_ts: list[datetime] = []
    out_open: list[Decimal] = []
    out_high: list[Decimal] = []
    out_low: list[Decimal] = []
    out_close: list[Decimal] = []
    out_volume: list[Decimal] = []
    out_quote_volume: list[Decimal | None] = []

    idx = 0
    for ts_open in opens:
        session = _session_containing(ts_open, sessions)
        window_end = min(ts_open + step, session.close_at)
        while idx < n and columns.ts[idx] < ts_open:
            idx += 1
        j = idx
        first_open: Decimal | None = None
        last_close = Decimal("0")
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        volume_sum = Decimal("0")
        quote_volumes: list[Decimal] = []
        quote_volume_known = True
        while j < n and columns.ts[j] < window_end:
            if first_open is None:
                first_open = columns.open[j]
            last_close = columns.close[j]
            highs.append(columns.high[j])
            lows.append(columns.low[j])
            volume_sum += columns.volume[j]
            qv = columns.quote_volume[j]
            if qv is None:
                quote_volume_known = False
            else:
                quote_volumes.append(qv)
            j += 1

        if first_open is None:
            idx = j
            continue  # gap: skip this derived candle instead of filling with 0/NaN

        out_ts.append(ts_open)
        out_open.append(first_open)
        out_high.append(max(highs))
        out_low.append(min(lows))
        out_close.append(last_close)
        out_volume.append(volume_sum)
        out_quote_volume.append(
            sum(quote_volumes, start=Decimal("0")) if quote_volume_known else None
        )
        idx = j

    result_columns = CandleColumns(
        ts=out_ts,
        open=out_open,
        high=out_high,
        low=out_low,
        close=out_close,
        volume=out_volume,
        quote_volume=out_quote_volume,
    )
    return RollupResult(columns=result_columns, rollup_version=ROLLUP_VERSION)


def _empty_columns() -> CandleColumns:
    return CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])
