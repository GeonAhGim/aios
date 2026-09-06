"""FA-5 `application/resolve_context.py` 단위테스트 — 페이크 저장소, DB 없음.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5
(§9 FA-5 DoD "컨텍스트 없는 쓰기 정적 검사 0건", §2.1 application 행).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    ResolveContextRequest,
    resolve_context,
)
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.defaults import build_default_hierarchy


@dataclass
class _FakeEntityRepository:
    """`resolve_context.EntityRepository` 부분 계약을 만족하는 인메모리 페이크
    — DB 없이 fail-closed 분기(없음/폐쇄됨/교차 테넌트)를 전부 재현한다."""

    legal_entities: dict[UUID, LegalEntity] = field(default_factory=dict)
    funds: dict[UUID, Fund] = field(default_factory=dict)
    portfolios: dict[UUID, Portfolio] = field(default_factory=dict)
    sub_accounts: dict[UUID, SubAccount] = field(default_factory=dict)

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        e = self.legal_entities.get(entity_id)
        return e if e is not None and e.tenant_id == tenant_id else None

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        f = self.funds.get(fund_id)
        if f is None:
            return None
        entity = self.legal_entities.get(f.entity_id)
        return f if entity is not None and entity.tenant_id == tenant_id else None

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        p = self.portfolios.get(portfolio_id)
        if p is None:
            return None
        fund = self.funds.get(p.fund_id)
        if fund is None:
            return None
        entity = self.legal_entities.get(fund.entity_id)
        return p if entity is not None and entity.tenant_id == tenant_id else None

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        s = self.sub_accounts.get(sub_account_id)
        if s is None:
            return None
        portfolio = self.portfolios.get(s.portfolio_id)
        if portfolio is None:
            return None
        fund = self.funds.get(portfolio.fund_id)
        if fund is None:
            return None
        entity = self.legal_entities.get(fund.entity_id)
        return s if entity is not None and entity.tenant_id == tenant_id else None


def _seeded_repo(user_id: UUID, tenant_id: UUID) -> tuple[_FakeEntityRepository, object]:
    hierarchy = build_default_hierarchy(
        user_id=user_id,
        tenant_id=tenant_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref="venue-acct-1",
        inception=date(2026, 1, 1),
    )
    repo = _FakeEntityRepository()
    repo.legal_entities[hierarchy.legal_entity.entity_id] = hierarchy.legal_entity
    repo.funds[hierarchy.fund.fund_id] = hierarchy.fund
    repo.portfolios[hierarchy.portfolio.portfolio_id] = hierarchy.portfolio
    repo.sub_accounts[hierarchy.sub_account.sub_account_id] = hierarchy.sub_account
    return repo, hierarchy


async def test_resolve_context_returns_full_context_for_bootstrapped_user():
    user_id = uuid4()
    tenant_id = uuid4()
    repo, hierarchy = _seeded_repo(user_id, tenant_id)

    request = ResolveContextRequest(tenant_id=tenant_id, user_id=user_id)
    context = await resolve_context(repo, request)

    assert context.tenant_id == tenant_id
    assert context.legal_entity_id == hierarchy.legal_entity.entity_id
    assert context.fund_id == hierarchy.fund.fund_id
    assert context.portfolio_id == hierarchy.portfolio.portfolio_id
    assert context.sub_account_id == hierarchy.sub_account.sub_account_id


async def test_resolve_context_rejects_unbootstrapped_user():
    """negative — 4단 계층이 하나도 없는(부트스트랩 안 된) 사용자는 값을
    추측하지 않고 fail-closed로 거부된다."""
    repo = _FakeEntityRepository()

    with pytest.raises(EntityContextResolutionError):
        await resolve_context(repo, ResolveContextRequest(tenant_id=uuid4(), user_id=uuid4()))


async def test_resolve_context_rejects_cross_tenant_lookup():
    """negative — 다른 테넌트의 tenant_id로 조회하면 존재하는 사용자라도
    거부된다(404 동형 원칙, FA-2 조회 관례와 동일)."""
    user_id = uuid4()
    tenant_id = uuid4()
    repo, _ = _seeded_repo(user_id, tenant_id)

    with pytest.raises(EntityContextResolutionError):
        await resolve_context(repo, ResolveContextRequest(tenant_id=uuid4(), user_id=user_id))


async def test_resolve_context_rejects_closed_fund():
    """negative — 상위 계층 하나가 폐쇄되면(예: Fund) 하위가 멀쩡해도 전체
    해석을 거부한다 — 쓰기 컨텍스트로 폐쇄된 엔티티를 쓸 수 없다."""
    user_id = uuid4()
    tenant_id = uuid4()
    repo, hierarchy = _seeded_repo(user_id, tenant_id)
    closed_fund = hierarchy.fund.model_copy(update={"closed_at": datetime.now(timezone.utc)})
    repo.funds[closed_fund.fund_id] = closed_fund

    with pytest.raises(EntityContextResolutionError):
        await resolve_context(repo, ResolveContextRequest(tenant_id=tenant_id, user_id=user_id))
