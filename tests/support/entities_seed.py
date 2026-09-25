"""Shared seed for a test tenant's FA-1 default entity hierarchy.

FA-0d (`cdb114b6903f`, task-1943) fails the whole migration on the spot if a
single `pos_snapshot` row cannot be re-keyed (NULL `portfolio_id`, or a key
that is not the 4-part legacy / 5-part current format) -- the correct
fail-closed behaviour. Since FA-0d-fix (task-771991202) the snapshot adapter
writes `portfolio_id` as a real FK and fences it on tenant ownership, so
every test that opens a position needs the tenant's *actual* default
portfolio to exist: `create_test_tenant()` calls `bootstrap_default_hierarchy`
by default, and tests build keys with `default_portfolio_id(tenant_id)`.

This module is a thin adapter over the production
`ensure_default_hierarchy` (idempotent: safe to call again for a tenant that
already has some or all of the four levels) -- no test-only seeding rule.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.ensure_default_hierarchy import (
    ensure_default_hierarchy,
)
from src.foundation.entities.domain.defaults import DefaultHierarchy


async def bootstrap_default_hierarchy(pool: asyncpg.Pool, tenant_id: UUID) -> DefaultHierarchy:
    """Persist (idempotently) the FA-1 default legal entity / fund / portfolio /
    sub-account for `tenant_id` (a PERSONAL tenant, so `user_id == tenant_id`)
    and return the rows as persisted."""
    return await ensure_default_hierarchy(
        PostgresEntityRepository(pool),
        user_id=tenant_id,
        tenant_id=tenant_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref=f"test-default-venue-{tenant_id.hex[:8]}",
        inception=date(2026, 1, 1),
    )


async def bootstrap_default_portfolio(pool: asyncpg.Pool, tenant_id: UUID) -> UUID:
    """Convenience: bootstrap the hierarchy and return its `portfolio_id`
    (== `default_portfolio_id(tenant_id)`)."""
    hierarchy = await bootstrap_default_hierarchy(pool, tenant_id)
    return hierarchy.portfolio.portfolio_id
