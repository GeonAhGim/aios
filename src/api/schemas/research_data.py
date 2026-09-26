"""RD-17 frontend wiring -- research-data search/source-status HTTP API
response schemas.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1(contracts),
1:1 with frontend/packages/shared-types/src/researchData.ts(ResearchItemView/
ResearchSearchResponse/ResearchSourceStatusView) -- task-7775. This view is a
subset of `contracts/v1.ResearchItem` (excludes language/hash/revision_of/
body_ref) with the RD-5 entity-link result (instrument_id/unmapped_reason)
layered on top -- an API-only shape, so it does not live in contracts/v1.py
(same convention as market_data.py's CandleSeriesView).

Source status has exactly the same fields as `contracts/v1.SourceMeta`, so it
is re-exposed as-is instead of a new model (list_sources_endpoint returns
`ApiResponse[list[SourceMeta]]` directly).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.foundation.research_data.contracts.v1 import ResearchItemKind
from src.foundation.research_data.domain.entity_link import UnmappedReason

__all__ = ["ResearchItemView", "ResearchSearchResponseView"]


class ResearchItemView(BaseModel):
    item_id: UUID
    source_id: str
    kind: ResearchItemKind
    title: str
    url: str
    published_at: AwareDatetime
    known_at: AwareDatetime
    instrument_id: str | None
    unmapped_reason: UnmappedReason | None


class ResearchSearchResponseView(BaseModel):
    items: list[ResearchItemView]
    total: int
    truncated: bool
