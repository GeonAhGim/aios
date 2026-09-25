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
    MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT,
    MAX_SAVED_SCREENERS_PER_TENANT,
    SavedScreenerView,
    ScreenAlertView,
    ScreenDefinition,
    SharedScreenerView,
)
from src.foundation.screener.ports.repository import (
    SavedScreenerLimitError,
    SavedScreenerNameConflictError,
    ScreenAlertLimitError,
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


def _shared_row_to_view(row: asyncpg.Record) -> SharedScreenerView:
    return SharedScreenerView(
        id=row["id"],
        screener_id=row["screener_id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        definition=ScreenDefinition.model_validate(json.loads(row["definition"])),
        version=row["version"],
        created_at=row["created_at"],
    )


def _alert_row_to_view(row: asyncpg.Record) -> ScreenAlertView:
    return ScreenAlertView(
        id=row["id"],
        tenant_id=row["tenant_id"],
        screener_id=row["screener_id"],
        operator=row["operator"],
        threshold=row["threshold"],
        status=row["status"],
        created_at=row["created_at"],
        triggered_at=row["triggered_at"],
        triggered_count=row["triggered_count"],
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


class PostgresSharedScreenerRepository:
    """`shared_screeners` — insert-only, immutable per-version rows (module
    docstring of `contracts.v1.SharedScreenerView`)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_version(
        self, *, tenant_id: UUID, screener_id: UUID, name: str, definition: ScreenDefinition
    ) -> SharedScreenerView:
        payload = json.dumps(definition.model_dump(mode="json"))
        async with self._pool.acquire() as conn, conn.transaction():
            # Serializes concurrent shares of the same screener so the next
            # version number is never assigned twice (standard 105 §2.2).
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(screener_id))
            next_version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM shared_screeners WHERE screener_id = $1",
                screener_id,
            )
            row = await conn.fetchrow(
                "INSERT INTO shared_screeners (screener_id, tenant_id, name, definition, version) "
                "VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING *",
                screener_id,
                tenant_id,
                name,
                payload,
                next_version,
            )
            if row is None:
                raise RuntimeError("shared_screeners INSERT ... RETURNING returned no row")
        return _shared_row_to_view(row)

    async def get_latest(self, screener_id: UUID) -> SharedScreenerView | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM shared_screeners WHERE screener_id = $1 "
                "ORDER BY version DESC LIMIT 1",
                screener_id,
            )
        return _shared_row_to_view(row) if row is not None else None

    async def list_versions(self, screener_id: UUID) -> tuple[SharedScreenerView, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM shared_screeners WHERE screener_id = $1 ORDER BY version",
                screener_id,
            )
        return tuple(_shared_row_to_view(row) for row in rows)


class PostgresScreenAlertRepository:
    """`screen_alerts` — no RLS (same precedent as `price_alerts`, FD-14):
    `list_active()` is a cross-tenant background-loop read, same convention
    as `AlertService.evaluate_all_active`."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self, *, tenant_id: UUID, screener_id: UUID, operator: str, threshold: int
    ) -> ScreenAlertView:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(tenant_id))
            count = await conn.fetchval(
                "SELECT count(*) FROM screen_alerts WHERE tenant_id = $1 AND status = 'ACTIVE'",
                tenant_id,
            )
            if count >= MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT:
                raise ScreenAlertLimitError(
                    f"tenant {tenant_id} already has {count} active screen alerts "
                    f"(max {MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT})"
                )
            row = await conn.fetchrow(
                "INSERT INTO screen_alerts (tenant_id, screener_id, operator, threshold) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                tenant_id,
                screener_id,
                operator,
                threshold,
            )
            if row is None:
                raise RuntimeError("screen_alerts INSERT ... RETURNING returned no row")
        return _alert_row_to_view(row)

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[ScreenAlertView, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM screen_alerts WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return tuple(_alert_row_to_view(row) for row in rows)

    async def get(self, tenant_id: UUID, alert_id: UUID) -> ScreenAlertView | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM screen_alerts WHERE tenant_id = $1 AND id = $2",
                tenant_id,
                alert_id,
            )
        return _alert_row_to_view(row) if row is not None else None

    async def cancel(self, tenant_id: UUID, alert_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE screen_alerts SET status = 'CANCELLED' "
                "WHERE tenant_id = $1 AND id = $2 AND status = 'ACTIVE'",
                tenant_id,
                alert_id,
            )
        return str(result) == "UPDATE 1"

    async def mark_triggered(self, alert_id: UUID, *, matched_count: int) -> ScreenAlertView | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE screen_alerts SET status = 'TRIGGERED', triggered_at = now(), "
                "triggered_count = $2 WHERE id = $1 AND status = 'ACTIVE' RETURNING *",
                alert_id,
                matched_count,
            )
        return _alert_row_to_view(row) if row is not None else None

    async def list_active(self) -> tuple[ScreenAlertView, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM screen_alerts WHERE status = 'ACTIVE'")
        return tuple(_alert_row_to_view(row) for row in rows)
