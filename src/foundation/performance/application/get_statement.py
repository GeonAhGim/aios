"""GetStatement/ListStatements 쿼리 — 테넌트 스코프 강제 + 안전 한계 문구.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49), 81번
§3 "labels estimates ... never calls result 'guaranteed'".

FA-6(docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-6, §9 table
row 120): both queries accept an optional `portfolio_id` scope. `performance_
statement` has no portfolio_id column yet — statements accumulate only at
the tenant level (PAPER `scope_ref` is today `str(user.user_id)`), and
per-leaf field additions belong to a migration after FA-8 (allocation), so
this leaf (no write-path expansion, PM decision) does not add one now. So
when `portfolio_id` is given, `resolve_portfolio_scope` (reusing the FA-5
single entry point) confirms ownership/openness fail-closed, then also
confirms that value matches the tenant's FA-1 default portfolio
(`default_portfolio_id`) — because that is the only portfolio any statement
existing today could belong to. Passing a different (but valid) portfolio
means no statement is actually attributed to that portfolio, so instead of
returning the whole tenant, it is rejected (prevents cross-portfolio
leakage, "no full-return fallback"). Omitting `portfolio_id` (existing
callers) makes this module behave byte-identically to the previous
leaf."""
from __future__ import annotations

from uuid import UUID

from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    EntityRepository,
    resolve_portfolio_scope,
)
from src.foundation.entities.domain.defaults import default_portfolio_id
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


async def _verify_portfolio_scope(
    entities: EntityRepository | None, tenant_id: UUID, portfolio_id: UUID
) -> None:
    if entities is None:
        raise EntityContextResolutionError(
            "portfolio_id 스코프에는 entities 저장소가 필요합니다(정적 검사 우회 방어)."
        )
    await resolve_portfolio_scope(entities, tenant_id, portfolio_id)
    if portfolio_id != default_portfolio_id(tenant_id):
        raise EntityContextResolutionError(
            f"portfolio_id={portfolio_id}: 이 포트폴리오에 귀속된 statement가 없습니다."
        )


async def get_statement(
    repo: PerformanceRepository,
    *,
    tenant_id: UUID,
    statement_id: UUID,
    portfolio_id: UUID | None = None,
    entities: EntityRepository | None = None,
) -> PerformanceStatementView:
    if portfolio_id is not None:
        await _verify_portfolio_scope(entities, tenant_id, portfolio_id)
    statement = await repo.get_statement(statement_id)
    if statement is None:
        raise StatementNotFoundError(str(statement_id))
    if statement.tenant_id != tenant_id:
        raise CrossTenantStatementAccessError(statement_id)
    return _with_safe_limitations(statement_to_view(statement))


async def list_statements(
    repo: PerformanceRepository,
    *,
    tenant_id: UUID,
    scope: str | None = None,
    portfolio_id: UUID | None = None,
    entities: EntityRepository | None = None,
) -> tuple[PerformanceStatementView, ...]:
    if portfolio_id is not None:
        await _verify_portfolio_scope(entities, tenant_id, portfolio_id)
    statements = await repo.list_statements(tenant_id=tenant_id, scope=scope)
    return tuple(_with_safe_limitations(statement_to_view(s)) for s in statements)
