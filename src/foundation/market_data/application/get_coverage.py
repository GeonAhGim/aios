"""DC-18a — coverage query surface for `GET /v1/foundation/market-data/coverage`.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.2
DC-18 (backend half; the frontend `CoverageBadge` half is task-2196).

Decision (task-2195): no new migration, no new bounded context — reuse
`domain/coverage/registry.merge_spans`(DC-6) for merging and
`ports/coverage_repository.CoverageRepository`(DC-8) for storage.

Bridges two incompatible `CoverageSpan` contracts that already coexist by
design (documented in `adapters/postgres_coverage_repository.py` and
`adapters/storage/hot_postgres.py`): the DC-8 storage type
(`ports/coverage_repository.CoverageSpan` — 2-tier `quality`, no
`asset_class`) and the DC-6 merge type
(`contracts/v2/coverage.CoverageSpan` — 3-tier `quality_grade`, requires
`asset_class`). `_to_merge_span` converts one row at a time using the
instrument's `asset_class` (DC-1, looked up via `InstrumentRepository`) and
a fixed quality mapping (`PROVISIONAL -> RAW`, `VALIDATED -> VALIDATED`;
storage has no third tier so `GOLD` never appears here). Unifying the two
contracts for real is out of this leaf's scope, same as the adapters that
already made this call.

Tenant scoping reuses `VenueRegistrySource`(DC-8 `entitlements`, already
wired for LA-24's `authorize_venue`) instead of a new per-instrument tenant
column (`instruments`/`coverage_spans` have none) — a venue the tenant has
no entitlement for folds into the same empty result as genuinely-uncovered
venues, so this endpoint never leaks "exists but not yours" vs. "doesn't
exist" (same existence-leak discipline as `application/read_api.py`).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan as MergeCoverageSpan
from src.foundation.market_data.contracts.v2.coverage import QualityGrade
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
    CoverageRepository,
)
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.market_data.ports.entitlement import VenueRegistrySource
from src.foundation.market_data.ports.instrument_repository import InstrumentRepository

__all__ = ["get_coverage"]

_QUALITY_TO_GRADE = {
    CoverageQuality.PROVISIONAL: QualityGrade.RAW,
    CoverageQuality.VALIDATED: QualityGrade.VALIDATED,
}


def _to_merge_span(span: StoredCoverageSpan, *, asset_class: AssetClass) -> MergeCoverageSpan:
    return MergeCoverageSpan(
        instrument_id=span.instrument_id,
        venue=span.venue,
        asset_class=asset_class,
        timeframe=span.timeframe,
        quality_grade=_QUALITY_TO_GRADE[span.quality],
        start_at=span.start,
        end_at=span.end,
    )


async def get_coverage(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    instrument_id: str,
    venue: Venue,
    timeframe: Timeframe,
    coverage_repo: CoverageRepository,
    instrument_repo: InstrumentRepository,
    venue_registry: VenueRegistrySource,
) -> list[MergeCoverageSpan]:
    """Merged coverage spans for `(instrument_id, venue, timeframe)`.

    Returns `[]` (never raises, never 500s) when: the tenant has no
    entitlement for `venue`, no spans are declared for this axis, or the
    instrument row is missing (defensive — `coverage_spans.instrument_id`
    is FK-constrained to `instruments`, so this only guards against a
    concurrent delete between the two reads)."""
    if venue not in await venue_registry.registered_venues(tenant_id):
        return []

    stored = [
        span
        for span in await coverage_repo.list_spans(conn, instrument_id, timeframe)
        if span.venue is venue
    ]
    if not stored:
        return []

    instrument = await instrument_repo.get(conn, instrument_id)
    if instrument is None:
        return []

    converted = [_to_merge_span(span, asset_class=instrument.asset_class) for span in stored]
    return merge_spans(converted)
