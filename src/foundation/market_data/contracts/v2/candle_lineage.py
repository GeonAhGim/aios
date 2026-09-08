"""DC-22 — candle provenance contract v2: source_kind + tick lineage.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-22, §9.10 DC-22.

`SourceKind` distinguishes candles a vendor delivered pre-aggregated
(`VENDOR`) from candles this platform derived from its own tick stream
(`TICK_DERIVED`, produced by `domain/aggregation/tick_to_candle.py`).
`TickLineage` is the per-candle provenance record that aggregation attaches
to each derived bar, so a reader can tell which trades built a bar without
re-running the aggregation.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from src.foundation.market_data.contracts.v2.microstructure import Nanoseconds, Seq

SCHEMA_VERSION: Literal["candle-lineage-v2"] = "candle-lineage-v2"

__all__ = ["SCHEMA_VERSION", "SourceKind", "TickLineage"]


class SourceKind(str, Enum):
    """Where a stored candle's OHLCV values came from."""

    VENDOR = "VENDOR"
    TICK_DERIVED = "TICK_DERIVED"


class TickLineage(BaseModel, frozen=True):
    """Provenance of one `TICK_DERIVED` candle.

    `TradeTick` (DC-19) has no `trade_id` field — `seq` is the venue-assigned
    per-event identifier DC-19's own docstring already uses to break
    `ts_event` ties, so DC-22 reuses `(ts_event, seq)` as a tick's identity
    here: `first_*`/`last_*` name the opening and closing tick of the
    window, and `tick_count` is the number of distinct (post-dedup) ticks
    folded into the candle.
    """

    source_kind: Literal[SourceKind.TICK_DERIVED] = SourceKind.TICK_DERIVED
    first_ts_event: Nanoseconds
    first_seq: Seq
    last_ts_event: Nanoseconds
    last_seq: Seq
    tick_count: int = Field(ge=1)
    schema_version: Literal["candle-lineage-v2"] = SCHEMA_VERSION
