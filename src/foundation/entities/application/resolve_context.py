"""FA-5 — `application/resolve_context.py`: 주문·포지션·원장 쓰기의 단일
컨텍스트 해석 진입점.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5
(§9 FA-5 DoD, §2.1 application 행 "resolve_context(request)가 모든 쓰기의
단일 진입").

새 해석 규칙을 만들지 않는다(PM decision) — id 산출은 FA-1
`domain/defaults.py`의 결정론 규칙(user_id 단일 인자 UUIDv5)을 그대로
재사용하고, 존재·폐쇄 확인은 FA-2 `adapters/postgres_repository.py`(또는
같은 부분 계약을 만족하는 어떤 저장소)의 조회 메서드를 그대로 쓴다. 이
모듈이 새로 정의하는 것은 "그 결과들을 fail-closed로 묶어 하나의
`EntityContext`로 돌려준다"는 조합 규칙뿐이다.

지금은 `tenant_id == user_id`인 FA-1 기본 계층(개인 단일계좌 UX) 해석만
지원한다 — 사용자가 명시적으로 fund/portfolio를 고르는 다법인 UX는
FA-6 이후 별도 요청 shape(`ResolveContextRequest`에 필드 추가, MINOR)로
확장한다.

해석 실패(부트스트랩 안 됨·폐쇄됨·교차 테넌트)는 값을 추측하거나 기본값으로
메우지 않고 `EntityContextResolutionError`를 던진다 — 이 예외를 받은
호출자(주문·포지션·원장 쓰기 진입점)는 그 쓰기를 계속 진행해서는 안 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from src.foundation.entities.contracts.v1 import (
    EntityContext,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
    default_sub_account_id,
)


class EntityContextResolutionError(ValueError):
    """FA-5 fail-closed — 엔티티 계층 4단 중 하나라도 없거나(부트스트랩
    안 됨) 폐쇄됐으면 값을 추측하지 않고 이 예외로 거부한다. 주문·포지션·
    원장 쓰기 진입점이 `entity_context`를 받지 못했을 때(예: 정적 검사
    우회 시도로 리터럴 `None`이 넘어온 경우)도 같은 예외를 재사용한다 —
    "컨텍스트가 없다"는 실패 모드는 해석 실패든 누락 전달이든 호출자
    입장에서 같은 조치(쓰기 거부)로 이어져야 하기 때문이다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class EntityRepository(Protocol):
    """`PostgresEntityRepository`(FA-2)가 만족하는 부분 계약 — 조회 4종만
    필요하다(생성·폐쇄는 이 유스케이스의 책임이 아니다)."""

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None: ...

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None: ...

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None: ...

    async def get_sub_account(
        self, tenant_id: UUID, sub_account_id: UUID
    ) -> SubAccount | None: ...


@dataclass(frozen=True)
class ResolveContextRequest:
    """개인 사용자 기본 계층(FA-1) 전용 요청 — `tenant_id`/`user_id`만
    있으면 4단 계층 id 전부가 결정론(UUIDv5)으로 정해진다."""

    tenant_id: UUID
    user_id: UUID


def _require_open(
    entity: LegalEntity | Fund | Portfolio | SubAccount | None,
    *,
    kind: str,
    entity_id: UUID,
) -> None:
    if entity is None:
        raise EntityContextResolutionError(
            f"{kind} {entity_id}가 존재하지 않거나(미부트스트랩) 다른 테넌트 소유입니다."
        )
    if entity.closed_at is not None:
        raise EntityContextResolutionError(
            f"{kind} {entity_id}는 폐쇄되어 쓰기 컨텍스트로 쓸 수 없습니다."
        )


async def resolve_context(
    repo: EntityRepository, request: ResolveContextRequest
) -> EntityContext:
    """모든 주문·포지션·원장 쓰기의 단일 진입점. 결정론 id(FA-1)로 4단
    계층을 조회하고, 하나라도 없거나 폐쇄됐으면 fail-closed로 거부한다."""
    entity_id = default_entity_id(request.user_id)
    fund_id = default_fund_id(request.user_id)
    portfolio_id = default_portfolio_id(request.user_id)
    sub_account_id = default_sub_account_id(request.user_id)

    entity = await repo.get_legal_entity(request.tenant_id, entity_id)
    _require_open(entity, kind="LegalEntity", entity_id=entity_id)
    fund = await repo.get_fund(request.tenant_id, fund_id)
    _require_open(fund, kind="Fund", entity_id=fund_id)
    portfolio = await repo.get_portfolio(request.tenant_id, portfolio_id)
    _require_open(portfolio, kind="Portfolio", entity_id=portfolio_id)
    sub_account = await repo.get_sub_account(request.tenant_id, sub_account_id)
    _require_open(sub_account, kind="SubAccount", entity_id=sub_account_id)

    return EntityContext(
        tenant_id=request.tenant_id,
        legal_entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        sub_account_id=sub_account_id,
    )


__all__ = [
    "EntityContextResolutionError",
    "EntityRepository",
    "ResolveContextRequest",
    "resolve_context",
]
