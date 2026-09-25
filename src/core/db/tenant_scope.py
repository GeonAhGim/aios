"""PLT-30 — RLS session variable binding: `tenant_transaction()` is the single entry point.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 (line 108),
§9 PLT-30. §10 Risk1 — Outside the transaction opened by this module (the ~40
existing services that only use `pool.acquire()`), `app.tenant_id` is never set,
so querying tables with RLS enabled (the 8 foundation tables M5 ENABLEd) returns
zero rows via that path. Hence M5 narrows the scope to only those 8 tables
(legacy tables have policies created but are not ENABLEd).

`SET LOCAL` is transaction-scoped, so it automatically reverts when the
transaction ends on either commit or rollback (per PostgreSQL docs) — the value
does not linger after the connection is returned to the pool.
tenant_id is passed by calling `set_config(name, value, true)` with a bound
parameter instead of string concatenation (no injection risk; the third argument
`true` is equivalent to LOCAL).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


@asynccontextmanager
async def tenant_transaction(
    pool: asyncpg.Pool, tenant_id: UUID | None
) -> AsyncIterator[asyncpg.Connection]:
    """Opens a connection bound to `app.tenant_id` at the transaction scope.

    When `tenant_id=None`, binds an empty string — this matches no tenant-owned
    rows (fail-closed), so the policy always returns zero rows. To also read
    system events (`tenant_id IS NULL`), use [[system_transaction]] instead.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)",
            "" if tenant_id is None else str(tenant_id),
        )
        yield conn


@asynccontextmanager
async def system_transaction(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Binds only `app.role='system'` (`app.tenant_id` remains empty string).

    Among M5 policies, only the additional branch
    `tenant_id IS NULL AND current_setting('app.role', true) = 'system'`
    (dedicated to foundation_audit_event) passes through, while tenant-owned
    rows remain zero rows just like [[tenant_transaction]](None).
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await conn.execute("SELECT set_config('app.role', 'system', true)")
        yield conn
