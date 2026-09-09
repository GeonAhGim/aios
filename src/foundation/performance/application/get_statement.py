"""GetStatement/ListStatements 쿼리 — 테넌트 스코프 강제 + 안전 한계 문구.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49), 81번
§3 "labels estimates ... never calls result 'guaranteed'".

FA-6(docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-6, §9 표
120행): 두 쿼리 모두 선택적 `portfolio_id` 스코프를 받는다. `performance_
statement`에는 아직 portfolio_id 컬럼이 없다 — statement는 tenant 단위로만
쌓이고(PAPER `scope_ref`는 오늘 `str(user.user_id)`), 리프별 필드 추가는
FA-8(allocation) 이후 마이그레이션의 몫이라 이 리프(쓰기 경로 확장 없음,
PM decision)에서 새로 만들지 않는다. 그래서 `portfolio_id`가 주어지면
`resolve_portfolio_scope`(FA-5 단일 진입점 재사용)로 소유·개방을 fail-closed
확인한 뒤, 그 값이 tenant의 FA-1 기본 포트폴리오(`default_portfolio_id`)와
일치하는지도 확인한다 — 오늘 존재하는 모든 statement가 귀속될 수 있는
유일한 포트폴리오이기 때문이다. 다른(하지만 유효한) 포트폴리오를 주면 그
포트폴리오에 실제로 귀속된 statement가 하나도 없다는 뜻이므로, tenant
전체를 돌려주는 대신 거부한다(교차 포트폴리오 유출 방지, "전체 반환
폴백 금지"). `portfolio_id`를 생략하면(기존 호출자) 이 모듈은 이전 리프와
바이트 동일하게 동작한다."""
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
