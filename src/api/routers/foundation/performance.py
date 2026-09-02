"""Performance Reporting API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49).

`scope=LIVE`는 아직 거부한다 — `PaperStatementInputAdapter`(L48)만 배선돼
있고 LIVE용 `StatementInputPort` 구현은 이 리프의 스콥이 아니다(있는 척
하지 않는다, reconciliation의 "never assume zero"와 같은 태도)."""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
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
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.adapters.paper_input_adapter import UnreconciledInputError
from src.foundation.performance.application.compute_statement import (
    MethodologyNotFoundError,
    compute_statement,
)
from src.foundation.performance.application.correct_statement import (
    CrossTenantStatementAccessError as CorrectCrossTenantError,
)
from src.foundation.performance.application.correct_statement import (
    StatementNotFoundError as CorrectStatementNotFoundError,
)
from src.foundation.performance.application.correct_statement import correct_statement
from src.foundation.performance.application.get_statement import (
    CrossTenantStatementAccessError,
    StatementNotFoundError,
    list_statements,
)
from src.foundation.performance.application.get_statement import (
    get_statement as get_statement_query,
)
from src.foundation.performance.contracts.v1 import ComputeStatementCommand
from src.foundation.performance.ports.repository import PerformanceRepository, StatementInputPort
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/performance-statements", tags=["foundation:performance"])


@router.post(":compute", status_code=status.HTTP_202_ACCEPTED)
async def post_compute_statement(
    body: ComputeStatementRequest,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    inputs: StatementInputPort = Depends(get_paper_statement_input_adapter),
    evidence_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> PerformanceStatementView:
    if body.scope != StatementScope.PAPER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "scope=LIVE는 아직 지원하지 않습니다."
        )

    cmd = ComputeStatementCommand(
        scope=body.scope,
        scope_ref=str(user.user_id),
        period_start=body.period_start,
        period_end=body.period_end,
        methodology_version=body.methodology_version,
    )
    try:
        return await compute_statement(
            repo,
            inputs,
            evidence_repo,
            tenant_id=user.user_id,
            cmd=cmd,
            trace_id=uuid4(),
        )
    except UnreconciledInputError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MethodologyNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("")
async def list_performance_statements(
    scope: StatementScope | None = None,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
) -> PerformanceStatementListResponse:
    statements = await list_statements(
        repo, tenant_id=user.user_id, scope=scope.value if scope is not None else None
    )
    return PerformanceStatementListResponse(statements=list(statements))


@router.get("/{statement_id}")
async def get_performance_statement(
    statement_id: UUID,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
) -> PerformanceStatementView:
    try:
        return await get_statement_query(repo, tenant_id=user.user_id, statement_id=statement_id)
    except StatementNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 statement입니다.") from exc
    except CrossTenantStatementAccessError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/{statement_id}:correct")
async def post_correct_statement(
    statement_id: UUID,
    body: CorrectStatementRequest,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
    evidence_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> PerformanceStatementView:
    try:
        return await correct_statement(
            repo,
            evidence_repo,
            tenant_id=user.user_id,
            statement_id=statement_id,
            reason=body.reason,
            trace_id=uuid4(),
        )
    except CorrectStatementNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 statement입니다.") from exc
    except CorrectCrossTenantError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
