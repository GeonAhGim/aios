"""FA-1/FA-2 -- idempotent bootstrap of a user's default entity hierarchy.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (§2.1,
§9 FA-1 DoD "existing single-account UX unchanged"). task-771991202
(FA-0d-fix, CTO decision (a) on task-2405).

Every position write now carries the FA-1 default `portfolio_id` (UUIDv5 of
the user id, `domain/defaults.py`) and `pos_snapshot.portfolio_id` is a real
FK to `portfolio` -- so a tenant whose default hierarchy was never persisted
cannot write a single snapshot. This module is the one place that turns the
pure FA-1 rule (`build_default_hierarchy`) into persisted rows, shared by the
test-tenant seed (`tests/support/entities_seed.py`) and by the operational
bootstrap path, so the two can never drift apart.

Idempotent by construction: each of the four levels is looked up (tenant
scoped, FA-2 `get_*`) and created only when missing, so calling it again for
an already bootstrapped user is a no-op that returns the persisted rows. It
never guesses ids -- the ids are deterministic (FA-1), which is what makes
the lookup-then-create shape safe without a stored mapping table.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.defaults import DefaultHierarchy, build_default_hierarchy


class EntityBootstrapRepository(Protocol):
    """Partial contract satisfied by `PostgresEntityRepository` (FA-2): the
    four tenant-scoped lookups plus the four creates."""

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None: ...

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity: ...

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None: ...

    async def create_fund(self, fund: Fund) -> Fund: ...

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None: ...

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio: ...

    async def get_sub_account(
        self, tenant_id: UUID, sub_account_id: UUID
    ) -> SubAccount | None: ...

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount: ...


async def ensure_default_hierarchy(
    repo: EntityBootstrapRepository,
    *,
    user_id: UUID,
    tenant_id: UUID,
    base_currency: Currency,
    jurisdiction: str,
    region_tag: str,
    venue_account_ref: str,
    inception: date,
) -> DefaultHierarchy:
    """Persist the FA-1 default hierarchy for `user_id` where (and only
    where) it is missing, and return the rows as they exist afterwards.
    Levels that already exist are returned untouched -- their persisted
    attributes win over the arguments given here."""
    wanted = build_default_hierarchy(
        user_id=user_id,
        tenant_id=tenant_id,
        base_currency=base_currency,
        jurisdiction=jurisdiction,
        region_tag=region_tag,
        venue_account_ref=venue_account_ref,
        inception=inception,
    )
    entity = await repo.get_legal_entity(tenant_id, wanted.legal_entity.entity_id)
    if entity is None:
        entity = await repo.create_legal_entity(wanted.legal_entity)
    fund = await repo.get_fund(tenant_id, wanted.fund.fund_id)
    if fund is None:
        fund = await repo.create_fund(wanted.fund)
    portfolio = await repo.get_portfolio(tenant_id, wanted.portfolio.portfolio_id)
    if portfolio is None:
        portfolio = await repo.create_portfolio(wanted.portfolio)
    sub_account = await repo.get_sub_account(tenant_id, wanted.sub_account.sub_account_id)
    if sub_account is None:
        sub_account = await repo.create_sub_account(wanted.sub_account)
    return DefaultHierarchy(
        legal_entity=entity, fund=fund, portfolio=portfolio, sub_account=sub_account
    )
