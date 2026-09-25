"""DC-5 — Coverage span persistence port.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-6, §4.1(fail-closed), §9.2 DC-5.

`domain/coverage/registry.py`(DC-6, not yet implemented) builds merge/query
logic (pure) on top of this Protocol — this file defines only the persistence
port and carries no merge rules. `CoverageSpan` is defined directly here as
the storage contract (see §2.1 table "venture×asset_class×TF×period×quality")
because DC-6 does not exist yet; DC-6 will consume this type. Overlap
prohibition is enforced by the DC-8 migration's EXCLUDE constraint.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.foundation.market_data.contracts.v1 import Timeframe, Venue


class CoverageQuality(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    VALIDATED = "VALIDATED"


class CoverageSpan(BaseModel):
    instrument_id: str
    venue: Venue
    timeframe: Timeframe
    quality: CoverageQuality
    start: AwareDatetime
    end: AwareDatetime


@runtime_checkable
class CoverageRepository(Protocol):
    async def upsert_span(self, conn: asyncpg.Connection, span: CoverageSpan) -> CoverageSpan:
        """Insertion of an overlapping span causes the adapter to raise a DB
        EXCLUDE constraint violation exception (§4.1) — merge logic belongs to
        `domain/coverage/registry.py`(DC-6); this port only persists."""
        ...

    async def list_spans(
        self, conn: asyncpg.Connection, instrument_id: str, timeframe: Timeframe
    ) -> list[CoverageSpan]:
        """Ordered by `start` ascending. Returns an empty list when no spans
        are declared — this is different from "fill the requested period with
        zeros" (§4.1 prohibits that); it simply means no coverage declaration
        exists."""
        ...
