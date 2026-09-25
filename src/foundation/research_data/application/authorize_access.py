"""RD-8 -- entitlement wiring for the HTTP read path.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.3
"권한 | 테넌트별 소스 접근은 DC-9 entitlement 정책을 그대로 사용", §9 RD-8
("교차 테넌트 404, 소스 권한 403").

DC-9 (`src/foundation/market_data/domain/entitlement/policy.py`) is typed
against `Venue`/`AssetClass` -- a closed trading-venue vocabulary that
research sources (`opendart`, `ecos`, `fred`, `gdelt`, ...) never belong to,
so this leaf cannot import that policy without redefining its types
(forbidden -- "재정의 금지", DC-9's own spec note). DC-27
(`domain/entitlement/source_contract.py`, `authorize_source`) is the
`source_id`-keyed extension of that same DC-9 lineage -- its own docstring
names OPENDART/ECOS as the exact adapters it was built to gate ("a future
OPENDART/ECOS ingest_source, D6 'not registered before a contract
exists'"). This module reuses DC-27's pure `authorize_source` +
`permits_use` and the existing `application/authorize_source_access.py`
port call verbatim (no reimplementation) -- the same pattern
`src/api/routers/market_data.py`'s `authorize_redistribution` already uses
for market-data sources.

The spec's two DoD failure axes are kept as two distinct exceptions on
purpose:
* cross-tenant / unknown item_id -> `ResearchItemNotFoundError` (404,
  existence non-disclosure -- an item owned by another tenant must look
  identical to a nonexistent item, same principle as LA-24
  `MarketDataNotFoundError`).
* source-level entitlement denial -> `ResearchSourceAccessDeniedError`
  (403) -- unlike tenant-scope mismatch this is *not* folded into 404: the
  item unambiguously exists and belongs to the caller's own tenant, so a
  404 here would misreport a real, actionable permission gap as
  nonexistence.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg

from src.foundation.market_data.api import DataUse, permits_use
from src.foundation.market_data.application.authorize_source_access import (
    authorize_source_access,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = [
    "ResearchItemNotFoundError",
    "ResearchSourceAccessDeniedError",
    "authorize_source_read",
    "require_item",
]

_NOT_FOUND_MESSAGE = "연구 항목을 찾을 수 없습니다."
_DENIED_MESSAGE = "이 소스에 대한 접근 권한이 없습니다."


class ResearchItemNotFoundError(Exception):
    """Missing item_id and an item owned by another tenant fold into the
    same 404 -- never distinguishable by status code."""


class ResearchSourceAccessDeniedError(Exception):
    """The tenant/platform lacks source-level entitlement (DC-27
    `SourceContractGrant.allowed=False`, or a scope that does not permit
    the requested `DataUse`) for this `source_id`. Always 403, never
    folded into 404 (see module docstring)."""


async def authorize_source_read(
    conn: asyncpg.Connection,
    source_id: str,
    *,
    repo: SourceContractRepository,
    clock: Callable[[], datetime],
    use: DataUse = DataUse.USER_OWN_DISPLAY,
) -> None:
    """Raises `ResearchSourceAccessDeniedError` unless `source_id` has a
    valid contract that permits `use`. Fail-closed: a missing contract row
    (`NOT_FOUND`), an out-of-window contract (`NOT_YET_VALID`/`EXPIRED`),
    and a contract whose `redistribution_scope` does not cover `use` all
    deny -- the caller never learns which."""
    grant = await authorize_source_access(conn, source_id, repo=repo, clock=clock)
    if not grant.allowed or grant.redistribution_scope is None:
        raise ResearchSourceAccessDeniedError(_DENIED_MESSAGE)
    if not permits_use(grant.redistribution_scope, use):
        raise ResearchSourceAccessDeniedError(_DENIED_MESSAGE)


def require_item(item: ResearchItem | None) -> ResearchItem:
    """`repo.get_item(tenant_id, item_id)` already scopes by tenant --
    `None` covers both "does not exist" and "belongs to another tenant"
    (RD-4 `get_item` docstring). This just turns that into the router's
    404 exception."""
    if item is None:
        raise ResearchItemNotFoundError(_NOT_FOUND_MESSAGE)
    return item
