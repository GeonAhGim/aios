"""L08 -- StrategyStateMemory contract: per-execution crossover/indicator
state that replaces the process-memory cache (§1 row 42), whose emptiness on
every process restart made the first tick's crossover leaves always False
even when valid history existed. L13 persists this object
(`strategy_execution_state`, conditional UPDATE by `state_version`, I11);
this module only defines the shape and the pure state transition.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 63, §3.2.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.core.strategy.indicator_key import parse_key
from src.core.strategy.market_state import MarketState

_UNSCOPED_TIMEFRAME = "_unscoped"


class StrategyStateMemory(BaseModel):
    schema_version: Literal["ssm-v1"] = "ssm-v1"
    execution_id: int
    state_version: int
    last_bar_time: dict[str, datetime] = Field(default_factory=dict)
    prev_values: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator("prev_values", mode="before")
    @classmethod
    def _reject_float_values(cls, v: object) -> object:
        if isinstance(v, dict):
            for key, val in v.items():
                if isinstance(val, float):
                    raise ValueError(f"prev_values[{key!r}] must be Decimal, not float")
        return v

    @field_validator("last_bar_time")
    @classmethod
    def _timestamps_are_aware(cls, v: dict[str, datetime]) -> dict[str, datetime]:
        for tf, ts in v.items():
            if ts.tzinfo is None:
                raise ValueError(f"last_bar_time[{tf!r}] must be tz-aware")
        return v


def _timeframe_of(key: str) -> str:
    """Keys without an explicit `@tf` suffix (grammar §3.1) aren't scoped to any
    entry in `bar_close_time`/`bar_times`, so they can never be validated
    against a recorded `last_bar_time` -- they carry forward unconditionally
    under this shared bucket rather than being silently dropped."""
    return parse_key(key).timeframe or _UNSCOPED_TIMEFRAME


def advance(
    memory: StrategyStateMemory,
    market_state: MarketState,
    bar_times: dict[str, datetime],
) -> StrategyStateMemory:
    """Pure: returns a new `StrategyStateMemory`, never mutates `memory`.

    `bar_times` is the caller's assertion of what `memory.last_bar_time`
    holds for each timeframe going into this tick -- the immediately
    preceding bar. A timeframe's values only carry into the returned
    `prev_values` when that assertion matches
    (`memory.last_bar_time.get(tf) == bar_times.get(tf)`); otherwise the bar
    was skipped (or the memory is stale), and that timeframe's entries are
    dropped so the next crossover check for it comes up stale rather than
    silently comparing across a gap (I3).
    """
    new_last_bar_time = dict(memory.last_bar_time)
    new_prev_values = dict(memory.prev_values)

    keys_by_tf: dict[str, list[str]] = {}
    for key in market_state.values:
        keys_by_tf.setdefault(_timeframe_of(key), []).append(key)

    for tf, keys in keys_by_tf.items():
        continuous = memory.last_bar_time.get(tf) == bar_times.get(tf)
        for key in keys:
            if continuous:
                new_prev_values[key] = market_state.values[key]
            else:
                new_prev_values.pop(key, None)
        close_time = market_state.bar_close_time.get(tf)
        if close_time is not None:
            new_last_bar_time[tf] = close_time

    return memory.model_copy(
        update={
            "last_bar_time": new_last_bar_time,
            "prev_values": new_prev_values,
            "state_version": memory.state_version + 1,
        }
    )


__all__ = ["StrategyStateMemory", "advance"]
