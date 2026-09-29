"""RD-8 -- research_data HTTP read API (search + single item) + DC-9-lineage
entitlement wiring.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.3, §9
RD-8 ("entitlement 연동(DC-9) + src/api/routers/research_data.py + 통합",
DoD "교차 테넌트 404, 소스 권한 403").

71번 §6 규칙대로 라우터는 auth/TenantContext 주입·transport validation·
application 호출만 한다. 이용권/소스계약 판정은
`application/authorize_access.py`(DC-27, DC-9 lineage -- see that module's
docstring for why DC-9's `Venue`-typed policy itself cannot be reused
as-is), PIT 필터는 `application/query.py`(RD-7)에 위임한다. 도메인 예외는
잡지 않는다 -- `exception_registry_foundation.py`가 봉투로 번역한다.

마운트 경로는 다른 foundation 라우터와 같은 네임스페이스
(`/v1/foundation/market-data`, `/v1/foundation/charting` 등)를 따른
`/v1/foundation/research/items*`다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.research_data import ResearchItemView, ResearchSearchResponseView
from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.domain.entitlement.source_contract import DataUse
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.application.authorize_access import (
    ResearchSourceAccessDeniedError,
    authorize_source_read,
    require_item,
)
from src.foundation.research_data.application.query import search as search_items
from src.foundation.research_data.contracts.v1 import ResearchItem, ResearchItemKind, SourceMeta
from src.foundation.research_data.domain.entity_link import UnmappedReason, extract_entity_key
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/research", tags=["foundation:research-data"])

# RD-17(task-7775) -- separate namespace (/v1/foundation/research-data/*)
# expected by the frontend's apiRoutes.ts. Different contract from RD-8's
# /v1/foundation/research/items* (single required source_id vs. cross-source
# search), so instead of adding to that router this is a second router with
# its own prefix (router_registry.py includes both).
search_router = APIRouter(prefix="/v1/foundation/research-data", tags=["foundation:research-data"])

_SEARCH_CANDIDATE_LIMIT = 200


def get_research_repository(pool: asyncpg.Pool = Depends(get_pool)) -> PostgresResearchRepository:
    return PostgresResearchRepository(pool)


def get_source_contract_repository() -> SourceContractRepository:
    """DC-27 -- stateless, same reasoning as `market_data.py`'s function of
    the same name: only looks up `source_id`, no pool needed."""
    return PostgresSourceContractRepository()


def _split(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    values = tuple(v.strip() for v in raw.split(",") if v.strip())
    return values or None


@router.get("/items")
async def search_items_endpoint(
    source_id: str,
    instruments: str | None = None,
    kinds: str | None = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    as_of: datetime | None = None,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    repo: PostgresResearchRepository = Depends(get_research_repository),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[list[ResearchItem]]:
    """`source_id`는 필수다 -- DC-27 소스 계약 판정이 소스 단위이기 때문에
    (§ module docstring), 소스를 밝히지 않은 전체 검색은 이 leaf의 스콥
    밖이다. `instruments`/`kinds`는 콤마 구분 문자열."""
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await authorize_source_read(
            conn, source_id, repo=source_contracts, clock=lambda: now, use=DataUse.USER_OWN_DISPLAY
        )
    candidates = await repo.list_by_tenant(
        context.tenant_id, source_id=source_id, limit=_SEARCH_CANDIDATE_LIMIT
    )
    span = (
        (published_from, published_to)
        if published_from is not None and published_to is not None
        else None
    )
    items = search_items(
        candidates,
        instruments=_split(instruments),
        kinds=_split(kinds),
        span=span,
        as_of=as_of,
    )
    return ok(list(items))


@router.get("/items/{item_id}")
async def get_item_endpoint(
    item_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    repo: PostgresResearchRepository = Depends(get_research_repository),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[ResearchItem]:
    """타 테넌트/미존재는 동형 404(`require_item`). 항목이 자기 테넌트
    것으로 확인된 *후에* 소스 권한을 판정해 403을 준다 -- 순서를 바꾸면
    "이 소스는 거부됐다"는 신호가 "이 item_id가 존재한다"는 사실을 먼저
    노출하게 된다."""
    item = require_item(await repo.get_item(context.tenant_id, item_id))
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await authorize_source_read(
            conn,
            item.source_id,
            repo=source_contracts,
            clock=lambda: now,
            use=DataUse.USER_OWN_DISPLAY,
        )
    return ok(item)


class ResearchSearchRequest(BaseModel):
    """1:1 with frontend/packages/shared-types/src/researchData.ts
    `ResearchSearchInput` (camelCase already converted to these field names by
    http.ts's keysToSnake before arrival)."""

    query: str
    kinds: list[ResearchItemKind] = []
    instrument_id: str | None = None
    as_of: datetime | None = None


def _unmapped_reason(item: ResearchItem) -> UnmappedReason:
    """Reuses the same classification rule as RD-5 `link_item` (RD-A4: never
    guess), but without calling a real resolver -- it only classifies "is
    there a deterministic key". If the caller already confirmed there is no
    link row in `research_item_instruments`, a present deterministic key means
    it either failed the resolver or the matching batch hasn't run yet
    (NOT_FOUND); no deterministic key at all is NO_DETERMINISTIC_KEY."""
    has_deterministic_key = any(extract_entity_key(raw) is not None for raw in item.instruments)
    return (
        UnmappedReason.NOT_FOUND if has_deterministic_key else UnmappedReason.NO_DETERMINISTIC_KEY
    )


def _to_item_view(item: ResearchItem, linked_instrument_id: UUID | None) -> ResearchItemView:
    return ResearchItemView(
        item_id=item.item_id,
        source_id=item.source_id,
        kind=item.kind,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        known_at=item.known_at,
        instrument_id=str(linked_instrument_id) if linked_instrument_id is not None else None,
        unmapped_reason=None if linked_instrument_id is not None else _unmapped_reason(item),
    )


@search_router.post("/search")
async def search_research_data_endpoint(
    body: ResearchSearchRequest,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    repo: PostgresResearchRepository = Depends(get_research_repository),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[ResearchSearchResponseView]:
    """RD-17(task-7775) -- the cross-source search that ResearchPage.tsx's
    search button calls. Unlike RD-8 (`/items`), does not require `source_id`
    -- instead it gathers candidates first as (tenant, all sources), then runs
    `authorize_source_read` individually for each distinct `source_id` that
    actually appears, silently filtering out items from unauthorized sources
    (the denial itself is never disclosed -- unlike the single-source lookup's
    403, here a denial is just part of the search result)."""
    now = datetime.now(timezone.utc)
    candidates = await repo.list_by_tenant_across_sources(
        context.tenant_id, limit=_SEARCH_CANDIDATE_LIMIT
    )

    authorized_sources: dict[str, bool] = {}
    allowed_candidates: list[ResearchItem] = []
    async with pool.acquire() as conn:
        for item in candidates:
            if item.source_id not in authorized_sources:
                try:
                    await authorize_source_read(
                        conn,
                        item.source_id,
                        repo=source_contracts,
                        clock=lambda: now,
                        use=DataUse.USER_OWN_DISPLAY,
                    )
                    authorized_sources[item.source_id] = True
                except ResearchSourceAccessDeniedError:
                    authorized_sources[item.source_id] = False
            if authorized_sources[item.source_id]:
                allowed_candidates.append(item)

    matched = search_items(
        allowed_candidates,
        kinds=body.kinds or None,
        as_of=body.as_of,
    )

    query = body.query.strip().lower()
    if query:
        matched = tuple(item for item in matched if query in item.title.lower())

    linked = await repo.get_linked_instrument_ids([item.item_id for item in matched])

    if body.instrument_id is not None:
        matched = tuple(
            item for item in matched if str(linked.get(item.item_id)) == body.instrument_id
        )

    views = [_to_item_view(item, linked.get(item.item_id)) for item in matched]
    return ok(
        ResearchSearchResponseView(
            items=views,
            total=len(views),
            truncated=len(candidates) >= _SEARCH_CANDIDATE_LIMIT,
        )
    )


@search_router.get("/sources")
async def list_research_sources_endpoint(
    repo: PostgresResearchRepository = Depends(get_research_repository),
) -> ApiResponse[list[SourceMeta]]:
    """RD-17(task-7775) -- source status panel. `research_sources` is a shared
    catalog with no tenant scope (see the adapter module's docstring), so it
    is exposed as-is without any entitlement check -- only actual content
    access (`/search`, `/items`) is gated by per-source contracts."""
    sources = await repo.list_sources()
    return ok(sources)
