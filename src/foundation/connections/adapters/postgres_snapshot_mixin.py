"""asyncpg implementation of ConnectionRepository — snapshot/health portion.

Spec: AIOSproject #74 §2/§5, #105 (concurrency standard).

task-1723 P1-D: Extracted snapshot/health methods from postgres_repository.py
(330 lines, exceeding P6's 300-line threshold) into this mixin (pure move,
following the same separation convention as KISWebSocketMixin).
`persist_snapshot_if_syncable()` is the actual defense point for CON-004
(concurrent revoke vs. sync race) — it re-asserts whether the connection is
still ACTIVE_READONLY/DEGRADED, then persists snapshot + health in a single
transaction with a row lock (`SELECT ... FOR UPDATE`). Initially this was a
two-step process: read via `get_connection()` first, then call
`insert_snapshot()` separately — a real TOCTOU gap where a revoke could
commit between the two round-trips (discovered during review, 2026-09-02).
Confirmed that only re-assertion + write within the same transaction closes
that gap, so they were merged into this method.

task-1718 P0-E — `persist_snapshot_if_syncable()` now requires `tenant_id`
and opens the connection via `tenant_transaction()` (PLT-30). Left an explicit
`AND tenant_id = $2` on the re-assertion SELECT — in this environment the
DATABASE_URL role is a superuser (rolbypassrls=true), so RLS alone cannot
block anything; this WHERE clause is the current practical defense line, while
tenant_transaction() serves as a second defense line for when the production
DSN switches to a non-superuser role (same rationale as
`transition_connection_state` in postgres_repository.py).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.db.tenant_scope import tenant_transaction
from src.foundation.connections.domain.models import (
    AccountSnapshot,
    ConnectionHealth,
    ConnectionState,
    HealthState,
    SnapshotValue,
)


def _row_to_snapshot(
    row: asyncpg.Record, values: tuple[SnapshotValue, ...] = ()
) -> AccountSnapshot:
    return AccountSnapshot(
        id=row["id"],
        connection_id=row["connection_id"],
        captured_at=row["captured_at"],
        provider_as_of=row["provider_as_of"],
        freshness=row["freshness"],
        currency=row["currency"],
        source_evidence_ref=row["source_evidence_ref"],
        values=values,
    )


def _row_to_health(row: asyncpg.Record) -> ConnectionHealth:
    return ConnectionHealth(
        connection_id=row["connection_id"],
        evaluated_at=row["evaluated_at"],
        state=HealthState(row["state"]),
        error_code=row["error_code"],
        retry_after=row["retry_after"],
        provider_trace_ref=row["provider_trace_ref"],
    )


class _SnapshotHealthMixin:
    """`PostgresConnectionRepository` inherits this — expects `self._pool`."""

    _pool: asyncpg.Pool

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        tenant_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            # CON-004 Real defense point — this SELECT locks the row so that
            # UPDATEs from revoke_connection()'s transition_connection_state() on
            # the same connection are blocked until this transaction commits
            # (same target row). No separate round-trip between re-assertion and
            # write means no TOCTOU gap.
            row = await conn.fetchrow(
                "SELECT state FROM account_connection WHERE id = $1 AND tenant_id = $2 "
                "FOR UPDATE",
                connection_id,
                tenant_id,
            )
            if row is None or row["state"] in (
                ConnectionState.REVOKED.value,
                ConnectionState.DISCONNECTED.value,
            ):
                raise ConcurrencyConflictError(
                    f"account_connection.id={connection_id}: sync 도중 연결이 "
                    "종료됐습니다(동시 처리 충돌) — 스냅샷을 저장하지 않습니다."
                )

            # CON-006 Last layer of defense — the application layer's
            # classify_provider_response() queries latest_snapshot outside this
            # transaction, so two concurrent syncs can both decide "this
            # provider_as_of is new" and reach this point. Instead of propagating
            # a UNIQUE(connection_id, provider_as_of, source_evidence_ref)
            # violation as an error, we absorb it via ON CONFLICT DO NOTHING +
            # re-fetch of the existing row — not a failure of "snapshot already
            # exists", but a normal outcome of duplicate responses.
            snapshot_row = await conn.fetchrow(
                "INSERT INTO account_snapshot (connection_id, provider_as_of, freshness, "
                " currency, source_evidence_ref) VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (connection_id, provider_as_of, source_evidence_ref) DO NOTHING "
                "RETURNING *",
                snapshot.connection_id,
                snapshot.provider_as_of,
                snapshot.freshness,
                snapshot.currency,
                snapshot.source_evidence_ref,
            )
            newly_inserted = snapshot_row is not None
            if snapshot_row is None:
                snapshot_row = await conn.fetchrow(
                    "SELECT * FROM account_snapshot WHERE connection_id = $1 "
                    "AND provider_as_of = $2 AND source_evidence_ref = $3",
                    snapshot.connection_id,
                    snapshot.provider_as_of,
                    snapshot.source_evidence_ref,
                )
            if newly_inserted:
                # Only write values when this transaction actually created a new
                # row — when ON CONFLICT DO NOTHING above re-fetched an existing
                # row (duplicate response), its values are already persisted.
                for value in snapshot.values:
                    await conn.execute(
                        "INSERT INTO account_snapshot_value "
                        "(snapshot_id, entity_type, entity_key, value) "
                        "VALUES ($1, $2, $3, $4)",
                        snapshot_row["id"],
                        value.entity_type,
                        value.entity_key,
                        value.value,
                    )
            value_rows = await conn.fetch(
                "SELECT entity_type, entity_key, value FROM account_snapshot_value "
                "WHERE snapshot_id = $1",
                snapshot_row["id"],
            )
            await conn.execute(
                "INSERT INTO connection_health (connection_id, state, error_code, "
                " retry_after, provider_trace_ref) VALUES ($1, $2, $3, $4, $5)",
                health.connection_id,
                health.state.value,
                health.error_code,
                health.retry_after,
                health.provider_trace_ref,
            )
            if row["state"] == ConnectionState.DEGRADED.value:
                await conn.execute(
                    "UPDATE account_connection SET state = $2 WHERE id = $1",
                    connection_id,
                    ConnectionState.ACTIVE_READONLY.value,
                )
        values = tuple(
            SnapshotValue(
                entity_type=v["entity_type"], entity_key=v["entity_key"], value=v["value"]
            )
            for v in value_rows
        )
        return _row_to_snapshot(snapshot_row, values)

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM account_snapshot WHERE connection_id = $1 "
                "ORDER BY captured_at DESC LIMIT 1",
                connection_id,
            )
            if row is None:
                return None
            value_rows = await conn.fetch(
                "SELECT entity_type, entity_key, value FROM account_snapshot_value "
                "WHERE snapshot_id = $1",
                row["id"],
            )
        values = tuple(
            SnapshotValue(
                entity_type=v["entity_type"], entity_key=v["entity_key"], value=v["value"]
            )
            for v in value_rows
        )
        return _row_to_snapshot(row, values)

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO connection_health (connection_id, state, error_code, "
                " retry_after, provider_trace_ref) VALUES ($1, $2, $3, $4, $5) RETURNING *",
                health.connection_id,
                health.state.value,
                health.error_code,
                health.retry_after,
                health.provider_trace_ref,
            )
        return _row_to_health(row)

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM connection_health WHERE connection_id = $1 "
                "ORDER BY evaluated_at DESC LIMIT 1",
                connection_id,
            )
        return _row_to_health(row) if row is not None else None
