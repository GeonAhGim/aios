"""LA-2 — market_data timeframe pure rules (length · alignment).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-2, §9.2 LA-2.

`Timeframe`/`SessionWindow` are re-exported as-is from the LA-1 contract (contracts/v1)
(FND-03: domain imports contracts but never redefines them). This module provides
pure functions only — no I/O or global clock calls, timestamps always passed as arguments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe

__all__ = [
    "Timeframe",
    "SessionWindow",
    "UnknownTimeframeError",
    "duration",
    "align_open",
    "expected_opens",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_DURATIONS: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}


class UnknownTimeframeError(ValueError):
    """`MD_TIMEFRAME_UNKNOWN` — timeframe not registered in `_DURATIONS`."""


def duration(tf: Timeframe) -> timedelta:
    """Length of one `tf` candle. Unregistered values raise `UnknownTimeframeError`."""
    try:
        return _DURATIONS[tf]
    except KeyError as exc:
        raise UnknownTimeframeError(f"알 수 없는 timeframe: {tf!r}") from exc


def align_open(ts: datetime, tf: Timeframe) -> datetime:
    """open_time of the `tf` candle that `ts` falls into (UTC, tz-aware).

    D1 aligns to UTC midnight boundary (§8.1 "D1 UTC basis"). All other timeframes
    use equal-interval alignment from the UNIX epoch (UTC midnight), so M1–H4
    boundaries all snap to clock times (hourly/5-min/15-min/30-min/1-hour/4-hour intervals).
    """
    if ts.tzinfo is None:
        raise ValueError("align_open은 tz-aware datetime만 받는다")
    ts_utc = ts.astimezone(timezone.utc)
    if tf is Timeframe.D1:
        return ts_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    step = duration(tf)
    elapsed = ts_utc - _EPOCH
    aligned_seconds = (elapsed // step) * step
    return _EPOCH + aligned_seconds


def expected_opens(
    start: datetime, end: datetime, tf: Timeframe, sessions: list[SessionWindow]
) -> list[datetime]:
    """List of `tf` candle open_times that open within `sessions` windows
    over the `[start, end)` interval (ascending, deduplicated).

    Never produces timestamps outside session boundaries — for each session,
    iterates only from the first candle aligned at `open_at` up to (but not
    including) `close_at`; if the aligned result precedes `open_at`, skips
    to the next candle step.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("expected_opens는 tz-aware datetime만 받는다")
    step = duration(tf)
    opens: list[datetime] = []
    for session in sessions:
        cursor = align_open(session.open_at, tf)
        if cursor < session.open_at:
            cursor += step
        while cursor < session.close_at:
            if start <= cursor < end:
                opens.append(cursor)
            cursor += step
    return sorted(set(opens))
