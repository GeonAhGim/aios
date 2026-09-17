"""AI-15 -- `read`-scope MCP tools (thin proxy).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1
(`tools_{read,research,propose,paper}.py`: "each tool calls exactly one REST
use case, zero logic"), §9 AI-15 DoD (I-08), depends on AI-14
(`factory/application/research_tools.py`).

`compute_indicator_tool` below is exactly one call to
`research_tools.compute_indicator` (AI-14's own "delegation only" leaf) --
the only work this module adds is the HTTP<->Python value marshalling
FastAPI itself requires (JSON arrays in, `numpy.ndarray` out), not a second
implementation of indicator math (IND-1 already owns that, two calls away).
`IndicatorError` -> 400 is the same error-shape decision `evaluate_proposal.py`
already made for `AI_PROPOSAL_SCHEMA`: a caller mistake (unknown indicator
name, bad param), not a server fault.

`build_router()` takes `require_scope` as a parameter instead of importing
it from `server.py` -- `server.py` imports this module to mount its router,
so importing back would be circular. This is the same inversion
`src/api/router_registry.py`'s own docstring describes for why routers keep
heavy imports inside a function body, applied one level further.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.core.indicators.registry import IndicatorError
from src.foundation.ai.factory.application import research_tools
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.api.mcp.server import ScopeDependency

__all__ = ["ComputeIndicatorRequest", "ComputeIndicatorResponse", "build_router"]


class ComputeIndicatorRequest(BaseModel):
    name: str
    columns: dict[str, list[float]]
    params: dict[str, int] | None = None


class ComputeIndicatorResponse(BaseModel):
    values: dict[str, list[float]]


def build_router(require_scope: Callable[[Scope], ScopeDependency]) -> APIRouter:
    router = APIRouter(prefix="/mcp/tools", tags=["mcp-read"])
    scope_dependency = require_scope(Scope.READ)

    @router.post("/compute_indicator", response_model=ComputeIndicatorResponse)
    async def compute_indicator_tool(
        body: ComputeIndicatorRequest,
        _token: AgentToken = Depends(scope_dependency),
    ) -> ComputeIndicatorResponse:
        try:
            result = research_tools.compute_indicator(body.name, body.columns, body.params)
        except IndicatorError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return ComputeIndicatorResponse(
            values={key: [float(x) for x in value] for key, value in result.items()}
        )

    return router
