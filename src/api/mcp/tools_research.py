"""AI-15 -- `research`-scope MCP tools (thin proxy).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1
(`tools_{read,research,propose,paper}.py`), §9 AI-15 DoD (I-08), depends on
AI-14 (`factory/application/research_tools.py`) and AI-11's experiment
ledger. §2.3 AI-14's own docstring names this leaf's future caller
explicitly: "AI-15's `tools_{read,research}.py` calls straight into this
module rather than the three modules below directly".

`run_backtest` is deliberately not exposed as an HTTP tool here.
`research_tools.run_backtest`'s `strategy: SignalSource` parameter is a
compiled-strategy Protocol (`on_bar(window, position) -> OrderIntent | None`)
-- not a JSON-serializable value. Turning a `script_source` string into a
callable `SignalSource` is DSL-12/AI-9's compiler responsibility, and no
leaf up to and including AI-14 exposes that compiler as a pure
`compile(script_source) -> SignalSource` port this module could delegate to
without adding a compile step of its own logic (I-08's "zero logic"
boundary). Wiring it in is left to a follow-up once such a port exists.

Every other function here (the five experiment lookups) takes only
JSON-serializable identifiers and is a single `await research_tools.*` call,
exactly the same "delegation only" shape AI-14's own docstring describes.
`tenant_id` is always the authenticated `AgentToken.tenant_id`, never a
request field -- an agent token issued to tenant A must not be able to read
tenant B's experiments by naming a different `tenant_id` in the request
body; that boundary is the token's own scope, not extra logic this module
invents (same "authorization the REST layer already enforces" I-08 allows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.foundation.ai.factory.application import research_tools
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope
from src.foundation.experiments.adapters.postgres_repository import (
    PostgresExperimentRepository,
)
from src.foundation.experiments.application.compare import (
    CorruptedReproductionSetError,
    NoReproductionsFoundError,
    ReproducibilityKeyMismatchError,
)
from src.foundation.experiments.application.query import ExperimentNotFoundError
from src.foundation.experiments.contracts.v1 import Experiment

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.api.mcp.scope_types import ScopeDependency

__all__ = [
    "ExperimentComparisonResponse",
    "ExperimentIdRequest",
    "ExperimentSetRequest",
    "ReproducibilityKeyRequest",
    "build_router",
]


class ExperimentIdRequest(BaseModel):
    experiment_id: UUID


class ReproducibilityKeyRequest(BaseModel):
    reproducibility_key: str


class ExperimentSetRequest(BaseModel):
    experiment_ids: list[UUID]


class ExperimentComparisonResponse(BaseModel):
    reproducibility_key: str
    inputs_hash: str
    experiments: tuple[Experiment, ...]
    metrics_by_experiment: dict[UUID, dict[str, Any]]
    metric_keys: tuple[str, ...]


def _repository(request: Request) -> PostgresExperimentRepository:
    pool: asyncpg.Pool = request.app.state.pool
    return PostgresExperimentRepository(pool)


def build_router(require_scope: Callable[[Scope], ScopeDependency]) -> APIRouter:
    router = APIRouter(prefix="/mcp/tools", tags=["mcp-research"])
    scope_dependency = require_scope(Scope.RESEARCH)

    @router.post("/get_experiment_context", response_model=Experiment)
    async def get_experiment_context_tool(
        body: ExperimentIdRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> Experiment:
        try:
            return await research_tools.get_experiment_context(
                _repository(request), token.tenant_id, body.experiment_id
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.post("/list_experiment_reproductions", response_model=list[Experiment])
    async def list_experiment_reproductions_tool(
        body: ReproducibilityKeyRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> tuple[Experiment, ...]:
        return await research_tools.list_experiment_reproductions(
            _repository(request), token.tenant_id, body.reproducibility_key
        )

    @router.post("/get_experiment_lineage", response_model=list[Experiment])
    async def get_experiment_lineage_tool(
        body: ExperimentIdRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> tuple[Experiment, ...]:
        try:
            return await research_tools.get_experiment_lineage(
                _repository(request), token.tenant_id, body.experiment_id
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.post("/compare_experiment_set", response_model=ExperimentComparisonResponse)
    async def compare_experiment_set_tool(
        body: ExperimentSetRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> research_tools.ExperimentComparison:
        try:
            return await research_tools.compare_experiment_set(
                _repository(request), token.tenant_id, body.experiment_ids
            )
        except ExperimentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ReproducibilityKeyMismatchError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.post("/compare_experiment_reproductions", response_model=ExperimentComparisonResponse)
    async def compare_experiment_reproductions_tool(
        body: ReproducibilityKeyRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> research_tools.ExperimentComparison:
        try:
            return await research_tools.compare_experiment_reproductions(
                _repository(request), token.tenant_id, body.reproducibility_key
            )
        except NoReproductionsFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except CorruptedReproductionSetError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return router
