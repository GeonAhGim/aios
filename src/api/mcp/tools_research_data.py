"""RD-16 -- `research_data_search` MCP tool (thin proxy).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-16
("MCP `research_data_search` 도구(얇은 프록시) + 적대적 테스트", depends on RD-8,
AI-15, DoD "I-08, PIT 유지"), AI-15's own spec
(docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1
`tools_{read,research,propose,paper}.py`: "each tool calls exactly one REST
use case, zero logic").

This module is registered into `src/api/mcp/server.py` alongside
`tools_{read,research,propose,paper}.py` -- RD-16 depends on AI-15's server,
it does not stand up a second one (the task's `src/mcp/tools/` path in the
originating task note does not match any module this tree actually has;
AI-15 already established `src/api/mcp/tools_*.py` as the one MCP-tool
location, so this leaf follows that convention rather than inventing a
second `src/mcp/` tree).

`research_data_search` calls exactly the same three application-layer steps
`src/api/routers/research_data.py`'s `search_items_endpoint` (RD-8) already
calls, in the same order -- DC-27 entitlement check
(`authorize_source_read`), tenant-scoped candidate fetch
(`repo.list_by_tenant`), then RD-7's PIT filter (`query.search`, RD-A1: an
item with `known_at > as_of` is never returned). It does not reimplement any
of the three (I-08's "zero logic" boundary) -- it is the HTTP router's own
call sequence, reached from the agent-token transport instead of the human
transport. `tenant_id` is always the authenticated `AgentToken.tenant_id`,
never a request field, for the same reason `tools_research.py` never takes
one on the wire (an agent token issued to tenant A must not be able to read
tenant B's items by naming a different `tenant_id`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope
from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.domain.entitlement.source_contract import DataUse
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.application.authorize_access import (
    ResearchSourceAccessDeniedError,
    authorize_source_read,
)
from src.foundation.research_data.application.query import search as search_items
from src.foundation.research_data.contracts.v1 import ResearchItem

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.api.mcp.scope_types import ScopeDependency

__all__ = ["ResearchDataSearchRequest", "build_router"]

_SEARCH_CANDIDATE_LIMIT = 200


class ResearchDataSearchRequest(BaseModel):
    source_id: str
    instruments: tuple[str, ...] | None = None
    kinds: tuple[str, ...] | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    as_of: datetime | None = None


def build_router(require_scope: Callable[[Scope], ScopeDependency]) -> APIRouter:
    router = APIRouter(prefix="/mcp/tools", tags=["mcp-research-data"])
    scope_dependency = require_scope(Scope.READ)

    @router.post("/research_data_search", response_model=list[ResearchItem])
    async def research_data_search_tool(
        body: ResearchDataSearchRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> list[ResearchItem]:
        pool: asyncpg.Pool = request.app.state.pool
        repo = PostgresResearchRepository(pool)
        source_contracts = PostgresSourceContractRepository()
        now = datetime.now(timezone.utc)
        try:
            async with pool.acquire() as conn:
                await authorize_source_read(
                    conn,
                    body.source_id,
                    repo=source_contracts,
                    clock=lambda: now,
                    use=DataUse.USER_OWN_DISPLAY,
                )
        except ResearchSourceAccessDeniedError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

        candidates = await repo.list_by_tenant(
            token.tenant_id, source_id=body.source_id, limit=_SEARCH_CANDIDATE_LIMIT
        )
        span = (
            (body.published_from, body.published_to)
            if body.published_from is not None and body.published_to is not None
            else None
        )
        items = search_items(
            candidates,
            instruments=body.instruments,
            kinds=body.kinds,
            span=span,
            as_of=body.as_of,
        )
        return list(items)

    return router
