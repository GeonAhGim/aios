"""L08 -- MarketState DTO: multi-timeframe indicator snapshot carrying its own
bar-close provenance so look-ahead can be checked (I1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 66, §3.2.
Key grammar is not re-implemented here -- `values` keys are validated via
`indicator_key.parse_key`, the single source of truth (row 62/65).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.core.strategy.indicator_key import IndicatorKeyError, parse_key


class MarketStateIntegrityError(Exception):
    """`INTEGRITY_FUTURE_DATA` -- a `bar_close_time` is later than `as_of` (I1)."""


class MarketState(BaseModel):
    schema_version: Literal["ms-v1"] = "ms-v1"
    as_of: datetime
    values: dict[str, Decimal] = Field(default_factory=dict)
    bar_close_time: dict[str, datetime] = Field(default_factory=dict)

    @field_validator("values", mode="before")
    @classmethod
    def _validate_values(cls, v: object) -> object:
        if not isinstance(v, dict):
            return v
        for key, val in v.items():
            if isinstance(val, float):
                raise ValueError(f"values[{key!r}] must be Decimal, not float")
            try:
                parse_key(key)
            except IndicatorKeyError as exc:
                raise ValueError(f"values has a malformed indicator key: {key!r}") from exc
        return v

    @field_validator("as_of")
    @classmethod
    def _as_of_is_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be tz-aware")
        return v

    @field_validator("bar_close_time")
    @classmethod
    def _bar_close_time_is_aware(cls, v: dict[str, datetime]) -> dict[str, datetime]:
        for tf, ts in v.items():
            if ts.tzinfo is None:
                raise ValueError(f"bar_close_time[{tf!r}] must be tz-aware")
        return v

    @classmethod
    def from_flat(cls, d: dict[str, float], *, as_of: datetime, tf: str = "1m") -> MarketState:
        return cls(
            as_of=as_of,
            values={k: Decimal(str(v)) for k, v in d.items()},
            bar_close_time={tf: as_of},
        )


def assert_no_future(state: MarketState) -> None:
    """I1: `bar_close_time[tf] <= as_of` for every timeframe, or fail-closed."""
    for tf, close_time in state.bar_close_time.items():
        if close_time > state.as_of:
            raise MarketStateIntegrityError(
                f"INTEGRITY_FUTURE_DATA: bar_close_time[{tf!r}]={close_time!r} "
                f"> as_of={state.as_of!r}"
            )


__all__ = ["MarketState", "MarketStateIntegrityError", "assert_no_future"]
