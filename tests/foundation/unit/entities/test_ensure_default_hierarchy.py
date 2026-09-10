"""`application/ensure_default_hierarchy.py` unit tests -- fake repository, no DB.

task-771991202 (FA-0d-fix): the shared bootstrap must be idempotent and must
only create the levels that are missing, because both the test-tenant seed and
the operational path call it without knowing what already exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

from src.data.models.base import Currency
from src.foundation.entities.application.ensure_default_hierarchy import (
    ensure_default_hierarchy,
)
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
    default_sub_account_id,
)


@dataclass
class _FakeRepository:
    legal_entities: dict[UUID, LegalEntity] = field(default_factory=dict)
    funds: dict[UUID, Fund] = field(default_factory=dict)
    portfolios: dict[UUID, Portfolio] = field(default_factory=dict)
    sub_accounts: dict[UUID, SubAccount] = field(default_factory=dict)
    creates: list[str] = field(default_factory=list)

    def _tenant_of_entity(self, entity_id: UUID) -> UUID | None:
        entity = self.legal_entities.get(entity_id)
        return None if entity is None else entity.tenant_id

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        entity = self.legal_entities.get(entity_id)
        return entity if entity is not None and entity.tenant_id == tenant_id else None

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity:
        assert entity.entity_id not in self.legal_entities, "duplicate legal_entity"
        self.legal_entities[entity.entity_id] = entity
        self.creates.append("legal_entity")
        return entity

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        fund = self.funds.get(fund_id)
        if fund is None or self._tenant_of_entity(fund.entity_id) != tenant_id:
            return None
        return fund

    async def create_fund(self, fund: Fund) -> Fund:
        assert fund.fund_id not in self.funds, "duplicate fund"
        self.funds[fund.fund_id] = fund
        self.creates.append("fund")
        return fund

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        portfolio = self.portfolios.get(portfolio_id)
        if portfolio is None or await self.get_fund(tenant_id, portfolio.fund_id) is None:
            return None
        return portfolio

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio:
        assert portfolio.portfolio_id not in self.portfolios, "duplicate portfolio"
        self.portfolios[portfolio.portfolio_id] = portfolio
        self.creates.append("portfolio")
        return portfolio

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        sub = self.sub_accounts.get(sub_account_id)
        if sub is None or await self.get_portfolio(tenant_id, sub.portfolio_id) is None:
            return None
        return sub

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount:
        assert sub_account.sub_account_id not in self.sub_accounts, "duplicate sub_account"
        self.sub_accounts[sub_account.sub_account_id] = sub_account
        self.creates.append("sub_account")
        return sub_account


async def _ensure(repo: _FakeRepository, user_id: UUID, *, venue_account_ref: str = "venue-1"):
    return await ensure_default_hierarchy(
        repo,
        user_id=user_id,
        tenant_id=user_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref=venue_account_ref,
        inception=date(2026, 1, 1),
    )


async def test_creates_all_four_levels_with_deterministic_ids_when_nothing_exists():
    repo = _FakeRepository()
    user_id = uuid4()

    hierarchy = await _ensure(repo, user_id)

    assert repo.creates == ["legal_entity", "fund", "portfolio", "sub_account"]
    assert hierarchy.legal_entity.entity_id == default_entity_id(user_id)
    assert hierarchy.fund.fund_id == default_fund_id(user_id)
    assert hierarchy.portfolio.portfolio_id == default_portfolio_id(user_id)
    assert hierarchy.sub_account.sub_account_id == default_sub_account_id(user_id)
    assert hierarchy.portfolio.fund_id == hierarchy.fund.fund_id


async def test_second_call_is_a_no_op_and_returns_the_persisted_rows():
    repo = _FakeRepository()
    user_id = uuid4()
    first = await _ensure(repo, user_id, venue_account_ref="venue-first")

    second = await _ensure(repo, user_id, venue_account_ref="venue-second")

    assert repo.creates == ["legal_entity", "fund", "portfolio", "sub_account"]
    assert second == first
    # persisted attributes win over the arguments of a later call
    assert second.portfolio.venue_account_ref == "venue-first"


async def test_partial_hierarchy_only_creates_the_missing_levels():
    repo = _FakeRepository()
    user_id = uuid4()
    complete = await _ensure(repo, user_id)
    # simulate a bootstrap that stopped after the fund (portfolio/sub_account missing)
    del repo.portfolios[complete.portfolio.portfolio_id]
    del repo.sub_accounts[complete.sub_account.sub_account_id]
    repo.creates.clear()

    hierarchy = await _ensure(repo, user_id)

    assert repo.creates == ["portfolio", "sub_account"]
    assert hierarchy.legal_entity == complete.legal_entity
    assert hierarchy.fund == complete.fund
    assert hierarchy.portfolio.portfolio_id == default_portfolio_id(user_id)


async def test_two_users_get_disjoint_hierarchies():
    repo = _FakeRepository()
    a = await _ensure(repo, uuid4())
    b = await _ensure(repo, uuid4())

    assert a.portfolio.portfolio_id != b.portfolio.portfolio_id
    assert len(repo.portfolios) == 2
