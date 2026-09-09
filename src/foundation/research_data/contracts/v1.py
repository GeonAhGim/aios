"""RD-2 -- research_data contract v1.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2,
§3 (contract summary), §4 RD-A1/RD-A3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

`domain/` imports this file, but this file does not import `domain/`
(standard 71 §4, the same principle as FND-03/LB-1/LC-1). Adding a field is
minor (standard 107, a default is required) -- removing a field or changing
its meaning requires a new `v2` module.

Every `datetime` field is `AwareDatetime`, rejecting naive values. A
`ResearchItem` without `known_at` cannot be constructed (§3, "an item
without known_at is rejected at storage") -- pydantic enforces it as a
required field, so `pydantic.ValidationError` blocks it before it ever
reaches the read/write path.
"""
from __future__ import annotations

import enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

__all__ = [
    "SCHEMA_VERSION",
    "ResearchItemKind",
    "RedistributionPolicy",
    "ResearchDataErrorCode",
    "ResearchItem",
    "SourceMeta",
]

SCHEMA_VERSION: Literal["v1"] = "v1"

ResearchItemKind = Literal["filing", "news", "macro", "alt"]
RedistributionPolicy = Literal["store_full", "store_excerpt", "link_only"]


class ResearchDataErrorCode(str, enum.Enum):
    """§3's error taxonomy verbatim -- inventing new codes is forbidden,
    this enum is the single source of truth. The actual exception classes
    are each owned by the domain leaf that implements that rule
    (RD-2 `known_at.PointInTimeViolationError`; RD-3 onward for
    redistribution/collector-adapter source availability and rate limit)."""

    POINT_IN_TIME_VIOLATION = "RD_POINT_IN_TIME_VIOLATION"  # 409, re-query with a new as_of
    REDISTRIBUTION_DENIED = "RD_REDISTRIBUTION_DENIED"  # 403, not retryable
    SOURCE_UNAVAILABLE = "RD_SOURCE_UNAVAILABLE"  # 503, retryable
    RATE_LIMITED = "RD_RATE_LIMITED"  # 429, retry after retry_after


class ResearchItem(BaseModel):
    """A single research item (the common envelope for filings, news, macro,
    and alt data).

    `known_at` is when the system could have known this fact -- backtests
    and strategies only ever read `known_at <= bar_ts` (§1 point-in-time
    integrity, RD-A1). The check itself is
    `domain/known_at.assert_point_in_time`'s job (this file only defines the
    contract). When `revision_of` is set, this item is a correction of that
    `item_id` (input for RD-3; this leaf does not enforce chain rules).
    """

    item_id: UUID
    source_id: str
    kind: ResearchItemKind
    published_at: AwareDatetime
    known_at: AwareDatetime
    instruments: tuple[str, ...]
    title: str
    body_ref: str | None
    url: str
    language: str
    hash: str
    revision_of: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SourceMeta(BaseModel):
    """Per-source metadata -- the basis for enforcing license/collection
    policy (RD-A3, input to RD-3).

    `rate_limit` is the allowed requests per minute (a declared token-bucket
    parameter; the actual limiting is the collector adapter's
    responsibility -- the same principle as `RateLimitSpec` in
    `market_data/ports/provider.py`). `coverage` is a human-readable
    coverage description (e.g. "2015-01-01~present, 5 min delay") --
    structured coverage determination is DC-19 through 22's job.
    """

    source_id: str
    publisher: str
    redistribution: RedistributionPolicy
    license_ref: str
    rate_limit: int
    coverage: str
    schema_version: Literal["v1"] = SCHEMA_VERSION
