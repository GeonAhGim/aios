"""Performance Reporting API — Rule §6: router handles only auth/injection/transport
validation/command invocation.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49).

`scope=LIVE` is rejected for now — only `PaperStatementInputAdapter`(L48) is wired;
the LIVE `StatementInputPort` implementation is out of scope for this leaf
(do not pretend it exists, same attitude as reconciliation's "never assume zero").
This rejection is not a domain rule but an API-layer fact about a missing adapter,
so it raises `exception_mapping.UnsupportedStatementScopeError`
(§9 PLT-21b decision, task-1217).

Domain exceptions are not caught here — `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` translates them in the global handler.
get_statement.py and correct_statement.py share the same name but define different
StatementNotFoundError/CrossTenantStatementAccessError classes, so
`EXCEPTION_MAP` registers both classes.

FA-6: an optional `portfolio_id` query parameter was added to both GET
endpoints (not a new route) — scope validation is done by
`get_statement.py`. Omitting it makes the response byte-identical to the
previous leaf."""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.exception_mapping import UnsupportedStatementScopeError
from src.api.deps import get_current_user, get_pool
from src.api.foundation_deps import (
    get_audit_event_repository,
    get_paper_statement_input_adapter,
    get_performance_repository,
)
from src.api.schemas.foundation.performance import (
    ComputeStatementRequest,
    CorrectStatementRequest,
    PerformanceStatementListResponse,
    PerformanceStatementView,
    StatementScope,
)
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import EntityRepository
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.application.compute_statement import compute_statement
from src.foundation.performance.application.correct_statement import correct_statement
from src.foundation.performance.application.get_statement import (
    get_statement as get_statement_query,
)
from src.foundation.performance.application.get_statement import list_statements
from src.foundation.performance.contracts.v1 import ComputeStatementCommand
from src.foundation.performance.ports.repository import PerformanceRepository, StatementInputPort
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/performance-statements", tags=["foundation:performance"])


def get_entity_repository(pool: asyncpg.Pool = Depends(get_pool)) -> EntityRepository:
    return PostgresEntityRepository(pool)


@router.post(":compute", status_code=status.HTTP_202_ACCEPTED)
async def post_compute_statement(
    body: ComputeStatementRequest,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    inputs: StatementInputPort = Depends(get_paper_statement_input_adapter),
    evidence_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[PerformanceStatementView]:
    if body.scope != StatementScope.PAPER:
        raise UnsupportedStatementScopeError("scope=LIVE는 아직 지원하지 않습니다.")

    cmd = ComputeStatementCommand(
        scope=body.scope,
        scope_ref=str(user.user_id),
        period_start=body.period_start,
        period_end=body.period_end,
        methodology_version=body.methodology_version,
    )
    result = await compute_statement(
        repo,
        inputs,
        evidence_repo,
        tenant_id=user.user_id,
        cmd=cmd,
        trace_id=uuid4(),
    )
    return ok(result)


@router.get("")
async def list_performance_statements(
    scope: StatementScope | None = None,
    portfolio_id: UUID | None = None,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    entities: EntityRepository = Depends(get_entity_repository),
) -> ApiResponse[PerformanceStatementListResponse]:
    statements = await list_statements(
        repo,
        tenant_id=user.user_id,
        scope=scope.value if scope is not None else None,
        portfolio_id=portfolio_id,
        entities=entities,
    )
    return ok(PerformanceStatementListResponse(statements=list(statements)))


@router.get("/{statement_id}")
async def get_performance_statement(
    statement_id: UUID,
    portfolio_id: UUID | None = None,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    entities: EntityRepository = Depends(get_entity_repository),
) -> ApiResponse[PerformanceStatementView]:
    result = await get_statement_query(
        repo,
        tenant_id=user.user_id,
        statement_id=statement_id,
        portfolio_id=portfolio_id,
        entities=entities,
    )
    return ok(result)


@router.post("/{statement_id}:correct")
async def post_correct_statement(
    statement_id: UUID,
    body: CorrectStatementRequest,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    evidence_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[PerformanceStatementView]:
    result = await correct_statement(
        repo,
        evidence_repo,
        tenant_id=user.user_id,
        statement_id=statement_id,
        reason=body.reason,
        trace_id=uuid4(),
    )
    return ok(result)
