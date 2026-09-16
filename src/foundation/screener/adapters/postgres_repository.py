"""asyncpg implementation of `SavedScreenerRepository` — U-1a storage.

Spec: task-2628(U-1a) decision, standard 105 (concurrency standard).

The per-tenant saved-screener cap (50) is a row-count limit, so it cannot be
expressed with the conditional-UPDATE (EvalPlanQual) pattern used by
mandates/postgres_repository.py. Instead, `pg_advisory_xact_lock` serializes
only concurrent save requests from the same tenant within the transaction
(other tenants never block each other), and the count is checked afterward.
Name duplication is instead handled entirely by
`uq_saved_screeners_tenant_name` (a schema UNIQUE constraint) — no separate
lock is needed for that; under a race, exactly one writer wins
(standard 105 §2.2, "a schema UNIQUE constraint guarantees a single owner").
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from src.foundation.screener.contracts.v1 import (
    MAX_SAVED_SCREENERS_PER_TENANT,
    SavedScreenerView,
    ScreenDefinition,
)
from src.foundation.screener.ports.repository import (
    SavedScreenerLimitError,
    SavedScreenerNameConflictError,
)


def _row_to_view(row: asyncpg.Record) -> SavedScreenerView:
    return SavedScreenerView(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        definition=ScreenDefinition.model_validate(json.loads(row["definition"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresSavedScreenerRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(
        self, *, tenant_id: UUID, name: str, definition: ScreenDefinition
    ) -> SavedScreenerView:
        payload = json.dumps(definition.model_dump(mode="json"))
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(tenant_id))
            count = await conn.fetchval(
                "SELECT count(*) FROM saved_screeners WHERE tenant_id = $1", tenant_id
            )
            if count >= MAX_SAVED_SCREENERS_PER_TENANT:
                raise SavedScreenerLimitError(
                    f"tenant {tenant_id} already has {count} saved screeners "
                    f"(max {MAX_SAVED_SCREENERS_PER_TENANT})"
                )
            try:
                row = await conn.fetchrow(
                    "INSERT INTO saved_screeners (tenant_id, name, definition) "
                    "VALUES ($1, $2, $3::jsonb) RETURNING *",
                    tenant_id,
                    name,
                    payload,
                )
            except asyncpg.UniqueViolationError as exc:
                raise SavedScreenerNameConflictError(
                    f"tenant {tenant_id} already has a saved screener named {name!r}"
                ) from exc
            if row is None:
                raise RuntimeError("saved_screeners INSERT ... RETURNING returned no row")
        return _row_to_view(row)

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[SavedScreenerView, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM saved_screeners WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return tuple(_row_to_view(row) for row in rows)

    async def get(self, tenant_id: UUID, screener_id: UUID) -> SavedScreenerView | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM saved_screeners WHERE tenant_id = $1 AND id = $2",
                tenant_id,
                screener_id,
            )
        return _row_to_view(row) if row is not None else None

    async def delete(self, tenant_id: UUID, screener_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM saved_screeners WHERE tenant_id = $1 AND id = $2",
                tenant_id,
                screener_id,
            )
        return str(result) == "DELETE 1"
