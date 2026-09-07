"""DC-19 — market microstructure(tick-level) contracts v2.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-19, §9.10 DC-19.

`TradeTick`, `QuoteL1`, `BookL2` are the tick-level counterparts of the
OHLC candle contracts in `contracts/v1.py`. They live in `v2/` (not `v1.py`)
because they are a new concept, not a MAJOR revision of an existing v1
type (107_contract_versioning_and_compatibility_standard_v1.0.md §3.3) —
`v1.py` stays untouched.

`ts_event` (exchange-side event time) and `ts_recv` (local receipt time)
are stored as **nanosecond-precision integer** epoch timestamps, not
`datetime`. A Python `datetime` truncates to microsecond precision and
would silently lose the sub-microsecond ordering information some venues
provide; storing an `int` keeps full precision and makes ordering by
`seq`/`ts_event` a plain integer comparison. `_validate_nanoseconds`
below rejects any integer outside the nanosecond magnitude range, which
is how second/millisecond/microsecond-unit mistakes (a common integration
bug when a venue's feed handler forgets to multiply) are caught before
they reach storage.

Not implemented here: this leaf defines the DTOs only. Deriving OHLC
candles from a set of `TradeTick`s is DC-22's `domain/aggregation/
tick_to_candle.py` — that module aggregates all ticks whose `ts_event`
falls in a candle's half-open interval `[start, start + timeframe)`
(the same half-open convention as `contracts/v2/coverage.py`'s
`CoverageSpan`) into a single OHLCV bar:

- `open`  = price of the tick with the smallest `(ts_event, seq)` in the
  window (the venue's own sequence number breaks ties when two ticks
  share `ts_event`).
- `high`/`low` = max/min `price` across all ticks in the window.
- `close` = price of the tick with the largest `(ts_event, seq)`.
- `volume` = sum of `size` across all ticks in the window.

`QuoteL1`/`BookL2` are not folded into OHLC candles — they are a
separate NBBO/depth signal that DC-22 may attach alongside a candle
(e.g. quote at candle close) but never uses to compute open/high/low/
close/volume. Building that aggregation, replaying gaps/duplicates/
out-of-order ticks deterministically, and recording `source_kind`
lineage is entirely DC-22's responsibility, not this contract's.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import ULID

SCHEMA_VERSION: Literal["microstructure-v2"] = "microstructure-v2"

# Nanosecond epoch timestamps for dates in the plausible trading-data range
# (2001-09-09T01:46:40Z .. 2286-11-20T17:46:40Z) fall in [1e18, 1e19). Second-,
# millisecond-, and microsecond-unit epoch values for the same date range are
# 1e9, 1e12, 1e15 respectively — all well below this floor — so a bare
# magnitude check catches the unit mistake without needing to know which
# smaller unit was mistakenly used.
_NANOSECOND_FLOOR = 1_000_000_000_000_000_000
_NANOSECOND_CEILING = 10_000_000_000_000_000_000


def _validate_nanoseconds(value: int) -> int:
    if not (_NANOSECOND_FLOOR <= value < _NANOSECOND_CEILING):
        raise ValueError(
            "timestamp must be a nanosecond-precision epoch integer "
            f"(expected {_NANOSECOND_FLOOR} <= value < {_NANOSECOND_CEILING}), got {value!r}"
        )
    return value


Nanoseconds = Annotated[int, AfterValidator(_validate_nanoseconds)]
Seq = Annotated[int, Field(ge=0)]


class Aggressor(str, Enum):
    """Which side initiated the trade (crossed the spread). `UNKNOWN` covers
    venues that do not disclose aggressor side on their public tick feed."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class TradeTick(BaseModel, frozen=True):
    """A single executed trade print."""

    instrument_id: ULID
    venue: Venue
    ts_event: Nanoseconds
    ts_recv: Nanoseconds
    seq: Seq
    price: Decimal
    size: Decimal
    aggressor: Aggressor
    schema_version: Literal["microstructure-v2"] = SCHEMA_VERSION


class QuoteL1(BaseModel, frozen=True):
    """Top-of-book (best bid/offer) snapshot."""

    instrument_id: ULID
    venue: Venue
    ts_event: Nanoseconds
    ts_recv: Nanoseconds
    seq: Seq
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    schema_version: Literal["microstructure-v2"] = SCHEMA_VERSION


class BookLevel(BaseModel, frozen=True):
    """One price level of a `BookL2` side."""

    price: Decimal
    size: Decimal


class BookL2(BaseModel, frozen=True):
    """Level-2 order book snapshot. `bids` must be sorted best-first
    (descending price) and `asks` must be sorted best-first (ascending
    price) — a book with levels out of price order is malformed and is
    rejected rather than silently re-sorted, since a wrong order upstream
    usually signals a feed-parsing bug worth surfacing, not papering over."""

    instrument_id: ULID
    venue: Venue
    ts_event: Nanoseconds
    ts_recv: Nanoseconds
    seq: Seq
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    schema_version: Literal["microstructure-v2"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _levels_sorted_best_first(self) -> BookL2:
        bid_prices = [level.price for level in self.bids]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError(
                "BookL2.bids must be sorted by price descending (best bid first)"
            )
        ask_prices = [level.price for level in self.asks]
        if ask_prices != sorted(ask_prices):
            raise ValueError(
                "BookL2.asks must be sorted by price ascending (best ask first)"
            )
        return self
