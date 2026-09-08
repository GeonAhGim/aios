"""DC-22 — deterministic tick/quote to candle aggregation with source_kind lineage.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-22, §9.10 DC-22.

Builds OHLCV candles from `TradeTick` (DC-19, `contracts/v2/microstructure.py`)
the same way `domain/aggregation/timeframe_rollup.py` (DC-10) builds them
from M1 candles: grid alignment (`align_open`/`expected_opens`, LA-2) and
session windows (`VenueCalendar`, LA-3) are entirely delegated, never
reimplemented here. Pure function — no I/O, no asyncpg.

Open/high/low/close follow the tie-break rule DC-19's own docstring already
specifies for this derivation: open = price of the tick with the smallest
`(ts_event, seq)` in the window, close = largest `(ts_event, seq)`,
high/low = max/min price across the window. `quote_volume` is the sum of
`price * size` — always known (unlike DC-10's M1 rollup, a tick always
carries both fields, so there is no "unknown source row" case to propagate
as `None`).

fail-closed rules (DoD):
- ticks out of `ts_event` order are rejected (`UnsortedTicksError`), never
  silently re-sorted — a silent sort could pair a later trade with an
  earlier window.
- exact duplicate ticks (same `(ts_event, seq)` — see `TickLineage`
  docstring for why `seq` stands in for `trade_id`) collapse to one and do
  not inflate `tick_count`.
- a window with zero ticks produces no candle (no zero-volume ghost bar) —
  same policy as DC-10's internal-gap handling.
- ticks outside every session window are excluded because only session-
  window-derived opens are ever aggregated over; there is no separate
  in-session check to reimplement (LA-3 delegation).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind, TickLineage
from src.foundation.market_data.contracts.v2.microstructure import TradeTick
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration, expected_opens

__all__ = [
    "TickToCandleResult",
    "UnsortedTicksError",
    "MixedSeriesError",
    "SessionNotFoundError",
    "ticks_to_candles",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class UnsortedTicksError(ValueError):
    """`MD_TICK_TO_CANDLE_UNSORTED_INPUT` — ticks not sorted by `ts_event`
    ascending. Rejected rather than sorted, so a caller's bug (e.g. a feed
    replay that lost ordering) surfaces instead of silently pairing the
    wrong ticks with a window."""


class MixedSeriesError(ValueError):
    """`MD_TICK_TO_CANDLE_MIXED_SERIES` — all ticks passed to one call must
    share a single `(venue, instrument_id)`; mixing series would silently
    blend two symbols' prints into one OHLCV bar."""


class SessionNotFoundError(ValueError):
    """`MD_TICK_TO_CANDLE_SESSION_NOT_FOUND` — an open time returned by
    `expected_opens` was not found again in the session list that produced
    it (internal invariant violation, defensively rejected)."""


@dataclass(frozen=True, slots=True)
class TickToCandleResult:
    """Output of one `ticks_to_candles` call. `lineage[i]` is the provenance
    of `columns` row `i` — the two are always the same length, so a caller
    can never read OHLCV without knowing which ticks built it."""

    columns: CandleColumns
    lineage: tuple[TickLineage, ...]
    source_kind: SourceKind = SourceKind.TICK_DERIVED


def _ts_event_to_utc(ts_event: int) -> datetime:
    # ts_event is a nanosecond epoch integer; datetime only resolves to
    # microseconds, so floor-divide (never round, never float) to stay
    # deterministic. This only affects which grid bucket/session a tick
    # falls into — the OHLCV math itself stays on the original
    # ts_event/seq integers.
    return _EPOCH + timedelta(microseconds=ts_event // 1_000)


def _sessions_between(
    calendar: VenueCalendar, start: datetime, end: datetime
) -> list[SessionWindow]:
    day = calendar.trading_day_of(start)
    end_day = calendar.trading_day_of(end)
    sessions: list[SessionWindow] = []
    while day <= end_day:
        sessions.extend(calendar.sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _session_containing(ts_open: datetime, sessions: list[SessionWindow]) -> SessionWindow:
    for session in sessions:
        if session.open_at <= ts_open < session.close_at:
            return session
    raise SessionNotFoundError(f"no session found for open_time={ts_open!r}")


def _dedupe_sorted(ticks: list[TradeTick]) -> list[TradeTick]:
    deduped: list[TradeTick] = []
    seen: set[tuple[int, int]] = set()
    for tick in ticks:
        identity = (tick.ts_event, tick.seq)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(tick)
    return deduped


def _empty_result() -> TickToCandleResult:
    empty = CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])
    return TickToCandleResult(columns=empty, lineage=())


def ticks_to_candles(
    ticks: list[TradeTick], tf: Timeframe, calendar: VenueCalendar
) -> TickToCandleResult:
    """Aggregate `ticks` (a single venue/instrument series) into `tf`
    candles. Same input always produces byte-identical output — no wall
    clock, no randomness, no float, no set/dict iteration order dependence
    (dedup/tie-break keys are `(int, int)` tuples, never string-hashed)."""
    if not ticks:
        return _empty_result()

    venue = ticks[0].venue
    instrument_id = ticks[0].instrument_id
    for tick in ticks:
        if tick.venue != venue or tick.instrument_id != instrument_id:
            raise MixedSeriesError(
                f"ticks_to_candles requires a single (venue, instrument_id) series: "
                f"got {tick.venue!r}/{tick.instrument_id!r} alongside {venue!r}/{instrument_id!r}"
            )

    for i in range(len(ticks) - 1):
        if ticks[i].ts_event > ticks[i + 1].ts_event:
            raise UnsortedTicksError(
                f"tick at index {i} (ts_event={ticks[i].ts_event}) is newer than "
                f"index {i + 1} (ts_event={ticks[i + 1].ts_event})"
            )

    deduped = _dedupe_sorted(ticks)
    tick_dt = [_ts_event_to_utc(t.ts_event) for t in deduped]

    step = duration(tf)
    range_start = tick_dt[0]
    range_end = tick_dt[-1] + step
    sessions = _sessions_between(calendar, range_start, range_end)
    opens = expected_opens(range_start, range_end, tf, sessions)

    out_ts: list[datetime] = []
    out_open: list[Decimal] = []
    out_high: list[Decimal] = []
    out_low: list[Decimal] = []
    out_close: list[Decimal] = []
    out_volume: list[Decimal] = []
    out_quote_volume: list[Decimal | None] = []
    lineage: list[TickLineage] = []

    n = len(deduped)
    idx = 0
    for ts_open in opens:
        session = _session_containing(ts_open, sessions)
        window_end = min(ts_open + step, session.close_at)
        while idx < n and tick_dt[idx] < ts_open:
            idx += 1
        j = idx

        first_key: tuple[int, int] | None = None
        first_price = Decimal("0")
        first_ts_event = 0
        first_seq = 0
        last_key: tuple[int, int] | None = None
        last_price = Decimal("0")
        last_ts_event = 0
        last_seq = 0
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        volume_sum = Decimal("0")
        quote_volume_sum = Decimal("0")
        tick_count = 0

        while j < n and tick_dt[j] < window_end:
            tick = deduped[j]
            key = (tick.ts_event, tick.seq)
            if first_key is None or key < first_key:
                first_key = key
                first_price = tick.price
                first_ts_event = tick.ts_event
                first_seq = tick.seq
            if last_key is None or key > last_key:
                last_key = key
                last_price = tick.price
                last_ts_event = tick.ts_event
                last_seq = tick.seq
            highs.append(tick.price)
            lows.append(tick.price)
            volume_sum += tick.size
            quote_volume_sum += tick.price * tick.size
            tick_count += 1
            j += 1

        if first_key is None:
            idx = j
            continue  # gap: no ticks in this window — never emit a zero-filled candle

        out_ts.append(ts_open)
        out_open.append(first_price)
        out_high.append(max(highs))
        out_low.append(min(lows))
        out_close.append(last_price)
        out_volume.append(volume_sum)
        out_quote_volume.append(quote_volume_sum)
        lineage.append(
            TickLineage(
                first_ts_event=first_ts_event,
                first_seq=first_seq,
                last_ts_event=last_ts_event,
                last_seq=last_seq,
                tick_count=tick_count,
            )
        )
        idx = j

    columns = CandleColumns(
        ts=out_ts,
        open=out_open,
        high=out_high,
        low=out_low,
        close=out_close,
        volume=out_volume,
        quote_volume=out_quote_volume,
    )
    return TickToCandleResult(columns=columns, lineage=tuple(lineage))
