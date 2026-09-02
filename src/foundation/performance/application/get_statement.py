"""GetStatement/ListStatements 쿼리 — 테넌트 스코프 강제 + 안전 한계 문구.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49), 81번
§3 "labels estimates ... never calls result 'guaranteed'"."""
from __future__ import annotations

from uuid import UUID

from src.foundation.performance.application.statement_projection import statement_to_view
from src.foundation.performance.contracts.v1 import PerformanceStatementView, StatementState
from src.foundation.performance.ports.repository import PerformanceRepository

_ESTIMATE_DISCLAIMER = (
    "이 값은 추정치이며 확정 성과·보장 수익을 의미하지 않습니다(ESTIMATED)."
)


class StatementNotFoundError(Exception):
    pass


class CrossTenantStatementAccessError(Exception):
    """72번 에러 taxonomy `AUTH_PERFORMANCE_SCOPE_DENIED` — 호출부가 403으로
    매핑한다. 존재 여부는 흘리지 않는다(73번 TRU-006과 동일 원칙)."""

    def __init__(self, statement_id: UUID) -> None:
        super().__init__(f"AUTH_PERFORMANCE_SCOPE_DENIED: {statement_id}")
        self.reason_code = "AUTH_PERFORMANCE_SCOPE_DENIED"


def _with_safe_limitations(view: PerformanceStatementView) -> PerformanceStatementView:
    if view.state != StatementState.ESTIMATED or _ESTIMATE_DISCLAIMER in view.limitations:
        return view
    return view.model_copy(update={"limitations": [*view.limitations, _ESTIMATE_DISCLAIMER]})


async def get_statement(
    repo: PerformanceRepository, *, tenant_id: UUID, statement_id: UUID
) -> PerformanceStatementView:
    statement = await repo.get_statement(statement_id)
    if statement is None:
        raise StatementNotFoundError(str(statement_id))
    if statement.tenant_id != tenant_id:
        raise CrossTenantStatementAccessError(statement_id)
    return _with_safe_limitations(statement_to_view(statement))


async def list_statements(
    repo: PerformanceRepository, *, tenant_id: UUID, scope: str | None = None
) -> tuple[PerformanceStatementView, ...]:
    statements = await repo.list_statements(tenant_id=tenant_id, scope=scope)
    return tuple(_with_safe_limitations(statement_to_view(s)) for s in statements)
