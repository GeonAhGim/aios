"""AI-16 -- `propose`-scope MCP tool (thin proxy over AI-9's `generate_proposal`).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1
(`tools_{read,research,propose,paper}.py`), §2.3 AI-9
(`application/generate_proposal.py`), §9 AI-16 DoD, §2.2
("`adapters/external_agent_provider.py`: any MCP client is a proposal
channel, not a model provider -- the pipeline is complete without a
built-in model").

`submit_proposal` is the MCP-facing shape of the "external agent" proposal
channel §2.2 names: the calling agent has already drafted
`script_source`/`hypothesis`/`data_scope`/`params` itself (no upstream LLM
call happens inside AIOS for this path -- `ExternalAgentProvider.generate()`
only echoes the request body back as `StructuredOutput`, see that module's
docstring). This tool assembles the three pieces `generate_proposal`
(AI-9) needs beyond the draft itself:

- `registry_version`: `DEFAULT_REGISTRY.registry_hash()`, the same
  server-computed value `src/api/routers/scripts.py` already uses for
  human-authored scripts -- never client-supplied (a caller cannot claim
  compatibility with a registry version that never existed).
- `coverage_spans`: looked up fresh from `market_data`'s coverage tables
  (`application/get_coverage.py`, DC-18a) for every `data_scope.instruments`
  x every venue the tenant is entitled to, then flattened -- never
  client-supplied either. `domain/proposal_rules.py::check_data_scope_covered`
  (AI-8) is the actual gate; if this tool trusted a client-reported coverage
  list instead, a malicious external agent could simply claim full coverage
  and bypass that gate entirely. This is the one place this "thin proxy"
  does more than marshal a single call -- assembling truthful coverage data
  is a data lookup, not a business/authorization decision I-08 reserves for
  the REST layer (the same "marshalling, not reimplementation" posture
  `tools_read.py`'s own docstring already claims for its
  JSON<->`numpy.ndarray` conversion).
- `created_by_token`: always `token.token_id` (the authenticated
  `AgentToken`, never a request field) -- the same "authorization is the
  token's own scope, not a client-supplied identifier" discipline
  `tools_research.py` already applies to `tenant_id`.

`GenerateProposalError` subclasses already carry the spec §3 error catalog's
`code`/`http_status` (see that module's own docstring) -- this tool maps
them onto the wire response unchanged rather than re-deriving the status
code, the same "the delegate raises, this tool just reads two fields off
it" style `evaluate_proposal.py`'s FastAPI callers already use elsewhere.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.foundation.ai.factory.adapters.postgres_proposal_repository import (
    PostgresProposalRepository,
)
from src.foundation.ai.factory.application.generate_proposal import (
    GenerateProposalError,
    ProposalCompileRejected,
    generate_proposal,
)
from src.foundation.ai.factory.contracts.v1 import DataScope, ProviderRef, StrategyProposal
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope
from src.foundation.ai.providers.adapters.external_agent_provider import (
    EXTERNAL_AGENT_PROMPT,
    ExternalAgentProvider,
)
from src.foundation.ai.providers.ports.model_provider import GenerationBudget
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.adapters.postgres_tenant_venues import PostgresTenantVenueSource
from src.foundation.market_data.application.get_coverage import get_coverage
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from src.api.mcp.server import ScopeDependency

__all__ = ["SubmitProposalRequest", "build_router"]

_EXTERNAL_AGENT_BUDGET = GenerationBudget(cost_cap=Decimal(0), max_output_tokens=1)


class SubmitProposalRequest(BaseModel):
    script_source: str
    hypothesis: str
    data_scope: DataScope
    params: dict[str, bool | int | float | str] = {}


async def _coverage_spans_for_data_scope(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    data_scope: DataScope,
    coverage_repo: PostgresCoverageRepository,
    instrument_repo: PostgresInstrumentRepository,
    venue_registry: PostgresTenantVenueSource,
) -> list[CoverageSpan]:
    venues = await venue_registry.registered_venues(tenant_id)
    spans: list[CoverageSpan] = []
    for instrument_id in sorted(data_scope.instruments):
        for venue in sorted(venues, key=lambda v: v.value):
            spans.extend(
                await get_coverage(
                    conn,
                    tenant_id=tenant_id,
                    instrument_id=instrument_id,
                    venue=venue,
                    timeframe=data_scope.tf,
                    coverage_repo=coverage_repo,
                    instrument_repo=instrument_repo,
                    venue_registry=venue_registry,
                )
            )
    return spans


def build_router(require_scope: Callable[[Scope], ScopeDependency]) -> APIRouter:
    router = APIRouter(prefix="/mcp/tools", tags=["mcp-propose"])
    scope_dependency = require_scope(Scope.PROPOSE)

    @router.post("/submit_proposal", response_model=StrategyProposal)
    async def submit_proposal_tool(
        body: SubmitProposalRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> StrategyProposal:
        pool: asyncpg.Pool = request.app.state.pool
        coverage_repo = PostgresCoverageRepository(pool)
        instrument_repo = PostgresInstrumentRepository(pool)
        venue_registry = PostgresTenantVenueSource(pool)

        async with pool.acquire() as conn:
            coverage_spans = await _coverage_spans_for_data_scope(
                conn,
                tenant_id=token.tenant_id,
                data_scope=body.data_scope,
                coverage_repo=coverage_repo,
                instrument_repo=instrument_repo,
                venue_registry=venue_registry,
            )

        provider = ExternalAgentProvider(
            {
                "script_source": body.script_source,
                "hypothesis": body.hypothesis,
                "data_scope": body.data_scope.model_dump(mode="json"),
                "params": body.params,
            }
        )

        try:
            return await generate_proposal(
                provider=provider,
                provider_ref=ProviderRef.EXTERNAL_AGENT,
                prompt=EXTERNAL_AGENT_PROMPT,
                budget=_EXTERNAL_AGENT_BUDGET,
                coverage_spans=coverage_spans,
                registry_version=DEFAULT_REGISTRY.registry_hash(),
                repository=PostgresProposalRepository(pool),
                created_by_token=token.token_id,
            )
        except ProposalCompileRejected as exc:
            raise HTTPException(
                exc.http_status,
                {
                    "code": exc.code,
                    "dsl_code": exc.dsl_code,
                    "line": exc.line,
                    "col": exc.col,
                    "message": str(exc),
                },
            ) from exc
        except GenerateProposalError as exc:
            raise HTTPException(exc.http_status, f"{exc.code}: {exc}") from exc

    return router
