"""Performance Reporting API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49).

`scope=LIVE`는 아직 거부한다 — `PaperStatementInputAdapter`(L48)만 배선돼
있고 LIVE용 `StatementInputPort` 구현은 이 리프의 스콥이 아니다(있는 척
하지 않는다, reconciliation의 "never assume zero"와 같은 태도). 이 거부는
도메인 규칙이 아니라 아직 배선되지 않은 어댑터 부재를 알리는 API 계층
사정이라 `exception_mapping.UnsupportedStatementScopeError`로 표현한다
(§9 PLT-21b decision, task-1217).

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다. get_statement.py와
correct_statement.py가 이름은 같지만 서로 다른
StatementNotFoundError/CrossTenantStatementAccessError 클래스를 각자
정의해두므로 `EXCEPTION_MAP`이 양쪽 클래스를 모두 등록한다."""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.exception_mapping import UnsupportedStatementScopeError
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
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
) -> ApiResponse[PerformanceStatementListResponse]:
    statements = await list_statements(
        repo, tenant_id=user.user_id, scope=scope.value if scope is not None else None
    )
    return ok(PerformanceStatementListResponse(statements=list(statements)))


@router.get("/{statement_id}")
async def get_performance_statement(
    statement_id: UUID,
    user: User = Depends(get_current_user),
    repo: PerformanceRepository = Depends(get_performance_repository),
) -> ApiResponse[PerformanceStatementView]:
    result = await get_statement_query(repo, tenant_id=user.user_id, statement_id=statement_id)
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
