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

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import get_tenant_context
from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.domain.entitlement.source_contract import DataUse
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.application.authorize_access import (
    authorize_source_read,
    require_item,
)
from src.foundation.research_data.application.query import search as search_items
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/research", tags=["foundation:research-data"])

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
            conn, item.source_id, repo=source_contracts, clock=lambda: now,
            use=DataUse.USER_OWN_DISPLAY,
        )
    return ok(item)
