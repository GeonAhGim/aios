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

That said, "looked up then created" is two round trips, not one atomic step
-- two concurrent callers for the *same* `user_id` (e.g. two in-flight
requests during signup retry) can both observe a level missing and both
attempt to create it. The loser's INSERT hits the level's unique constraint;
the adapter turns that into `ConcurrencyConflictError` (105 standard, `src/
core/db/conditional_write.py`) rather than letting the raw driver error leak.
`_get_or_create` below recovers by re-querying once, per that error's own
documented contract ("caller must re-query and retry") -- the winner's row
is what both callers converge on, so the function stays idempotent under
concurrency and not just under sequential retries.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Protocol, TypeVar
from uuid import UUID

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.defaults import DefaultHierarchy, build_default_hierarchy

_T = TypeVar("_T")


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


async def _get_or_create(
    get: Callable[[], Awaitable[_T | None]],
    create: Callable[[], Awaitable[_T]],
) -> _T:
    """Get-or-create for one hierarchy level, safe against a concurrent
    bootstrap racing us to create the same deterministic id. If `create`
    loses the race it raises `ConcurrencyConflictError` (105 standard); we
    recover by re-querying once, per that error's own contract. Re-raise if
    the row still is not there -- that means the failure was not actually a
    create-vs-create race and swallowing it would hide a real problem."""
    existing = await get()
    if existing is not None:
        return existing
    try:
        return await create()
    except ConcurrencyConflictError:
        winner = await get()
        if winner is None:
            raise
        return winner


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
    entity = await _get_or_create(
        lambda: repo.get_legal_entity(tenant_id, wanted.legal_entity.entity_id),
        lambda: repo.create_legal_entity(wanted.legal_entity),
    )
    fund = await _get_or_create(
        lambda: repo.get_fund(tenant_id, wanted.fund.fund_id),
        lambda: repo.create_fund(wanted.fund),
    )
    portfolio = await _get_or_create(
        lambda: repo.get_portfolio(tenant_id, wanted.portfolio.portfolio_id),
        lambda: repo.create_portfolio(wanted.portfolio),
    )
    sub_account = await _get_or_create(
        lambda: repo.get_sub_account(tenant_id, wanted.sub_account.sub_account_id),
        lambda: repo.create_sub_account(wanted.sub_account),
    )
    return DefaultHierarchy(
        legal_entity=entity, fund=fund, portfolio=portfolio, sub_account=sub_account
    )
