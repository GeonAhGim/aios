"""DC-6 — coverage span declaration contract v2.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-6, §4.1 (fail-closed, EXCLUDE constraint forbidding `coverage_spans`
overlap), §9.2 DC-6.

`domain/coverage/registry.py` (task-1127 decision) returns this type, and
DC-7 `domain/coverage/gaps.py`'s `plan_fetch` depends on that return type,
so it lives in contracts rather than domain (same principle as DC-1
`instruments.py` — the public contract's SSOT is contracts, not a pure
domain module).

A coverage declaration is keyed on the (venue x asset_class x TF x period x
quality_grade) axis. `instrument_id` was added for use as the query key
(§2.1 `coverage_for(instrument, tf)`) — since DC-1 `Instrument` has no
venue (venue belongs to `VenueListing`), this contract carries venue and
asset_class as separate fields.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, model_validator

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import ULID

SCHEMA_VERSION: Literal["coverage-v2"] = "coverage-v2"


class QualityGrade(str, Enum):
    """Trust grade of a declared coverage span. Unverified: the actual
    grading criteria per exchange/vendor are not based on any external
    contract — these three tiers are an internal grading scheme this
    project defines on its own."""

    RAW = "RAW"
    VALIDATED = "VALIDATED"
    GOLD = "GOLD"


class CoverageSpan(BaseModel, frozen=True):
    """`[start_at, end_at)` half-open interval — `end_at` is the exclusive
    upper bound.

    Two spans overlapping within the same (instrument_id, venue,
    asset_class, timeframe, quality_grade) axis is a state the DB EXCLUDE
    constraint (§4.1) rejects — it may transiently exist in a raw
    declaration list that hasn't gone through `domain/coverage/registry.py`
    merging, but it must be merged before persisting.
    """

    instrument_id: ULID
    venue: Venue
    asset_class: AssetClass
    timeframe: Timeframe
    quality_grade: QualityGrade
    start_at: AwareDatetime
    end_at: AwareDatetime
    schema_version: Literal["coverage-v2"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _start_before_end(self) -> CoverageSpan:
        if self.end_at <= self.start_at:
            raise ValueError(
                f"CoverageSpan requires start_at < end_at: "
                f"start_at={self.start_at!r}, end_at={self.end_at!r}"
            )
        return self
