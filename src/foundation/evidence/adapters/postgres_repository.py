"""asyncpg implementation of AuditEventRepository.

Spec: AIOSproject #79 §1, #105 (concurrency standard) — INSERT variant.

`append_event()` is the core of this module. The hash chain follows a
read-then-write pattern (read the previous event's hash, compute a new
hash linked to it), but `conditional_update` does not apply here since it
targets UPDATE-only workflows and there is no "existing row" to guard
against when every call inserts a new row. Instead, a Postgres advisory
lock serializes appends to the same tenant (or system) chain across
transactions, preventing concurrent requests from each appending a
different new event based on a stale view of the "last event".

F1(task-9420/task-9458) — `foundation_audit_event` has RLS ENABLE+FORCE
(b3c7f19ad2e6/c9f4e2a1b6d7); `append_event`/`list_timeline`/
`list_chain_for_verification` used `pool.acquire()` directly and never bound
`app.tenant_id` — harmless while DATABASE_URL is a superuser
(rolbypassrls=true), but once it switches to a non-superuser role
(`aios_app`) a correct tenant would see zero rows of its own audit trail.
Following the `connections`/`mandates`/`reconciliation` adapters' precedent,
those three methods now open their connection through
`tenant_transaction()`/`system_transaction()` (PLT-30,
[[src/core/db/tenant_scope.py]]) instead of a bare `pool.acquire()`; the
explicit `tenant_id = ...`/`tenant_id IS NULL` SQL conditions stay as the
real defense while the role is still a superuser. `append_event_in()` is
deliberately left untouched — it takes an already-open `conn` from callers
across `ledger`/`market_data`/`positions`/`services/oms` that manage their
own transaction scope; changing its contract is out of this leaf's scope.
`get_latest_event()` has no `tenant_id` parameter (its only caller,
`mandates/application/activate_revision.py`, looks up by
aggregate_type/aggregate_id/action only) and is left out of scope for the
same reason `connections/adapters/postgres_repository.py`'s
`get_connection()` was."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.core.db.tenant_scope import system_transaction, tenant_transaction
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import compute_event_hash


def _row_to_event(row: asyncpg.Record) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        sequence_no=row["sequence_no"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_revision=row["aggregate_revision"],
        action=row["action"],
        outcome=Outcome(row["outcome"]),
        actor_subject_id=row["actor_subject_id"],
        trace_id=row["trace_id"],
        payload_hash=row["payload_hash"],
        payload=json.loads(row["payload"]),
        classification=Classification(row["classification"]),
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
        occurred_at=row["occurred_at"],
    )


class PostgresAuditEventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append_event(
        self,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent:
        # F1(task-9458) — binds app.tenant_id (or app.role='system' when
        # tenant_id is None) before append_event_in's INSERT/SELECT run, so
        # the RLS policy on foundation_audit_event sees the right GUC.
        cm = (
            system_transaction(self._pool)
            if tenant_id is None
            else tenant_transaction(self._pool, tenant_id)
        )
        async with cm as conn:
            return await self.append_event_in(
                conn,
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_revision=aggregate_revision,
                action=action,
                outcome=outcome,
                actor_subject_id=actor_subject_id,
                trace_id=trace_id,
                payload_hash=payload_hash,
                payload=payload,
                classification=classification,
            )

    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent:
        """105 §5.1 — Executes inside a transaction (`conn`) already opened by
        the caller. Does not acquire its own connection or start a new
        transaction (avoids the §2 P1 "acquire a second connection while
        holding one" anti-pattern — if a parent transaction like
        `post_entry` fails after calling this function, this INSERT rolls
        back with it). `pg_advisory_xact_lock` releases only when the
        transaction ends, so `conn` must actually be inside a transaction
        for serialization to hold — caller's responsibility."""
        # Chain serialization point (#79 §1) — blocks appends to the same
        # tenant (or system) until this transaction ends. Using two int4
        # keys avoids namespace collisions with other advisory lock usages
        # (e.g., when another bounded context also uses tenant_id-based
        # locks) — standard Postgres idiom.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('foundation_audit_event'), hashtext($1))",
            str(tenant_id) if tenant_id is not None else "system",
        )

        if tenant_id is not None:
            prev_row = await conn.fetchrow(
                "SELECT sequence_no, event_hash FROM foundation_audit_event "
                "WHERE tenant_id = $1 ORDER BY sequence_no DESC LIMIT 1",
                tenant_id,
            )
        else:
            prev_row = await conn.fetchrow(
                "SELECT sequence_no, event_hash FROM foundation_audit_event "
                "WHERE tenant_id IS NULL ORDER BY sequence_no DESC LIMIT 1"
            )
        next_sequence_no = 1 if prev_row is None else prev_row["sequence_no"] + 1
        previous_hash = None if prev_row is None else prev_row["event_hash"]

        occurred_at = datetime.now(timezone.utc)
        event_hash = compute_event_hash(
            previous_hash=previous_hash,
            tenant_id=tenant_id,
            sequence_no=next_sequence_no,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            action=action,
            outcome=outcome,
            payload_hash=payload_hash,
            classification=classification,
            occurred_at=occurred_at,
        )

        row = await conn.fetchrow(
            "INSERT INTO foundation_audit_event "
            "(id, tenant_id, sequence_no, aggregate_type, aggregate_id, "
            " aggregate_revision, action, outcome, actor_subject_id, trace_id, "
            " payload_hash, payload, classification, previous_hash, event_hash, "
            " occurred_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, "
            "$13, $14, $15, $16) "
            "RETURNING *",
            uuid4(),
            tenant_id,
            next_sequence_no,
            aggregate_type,
            aggregate_id,
            aggregate_revision,
            action,
            outcome.value,
            actor_subject_id,
            trace_id,
            payload_hash,
            json.dumps(payload),
            classification.value,
            previous_hash,
            event_hash,
            occurred_at,
        )
        return _row_to_event(row)

    async def list_timeline(
        self,
        tenant_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        aggregate_type: str | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        conditions = ["tenant_id = $1"]
        params: list[object] = [tenant_id]
        if cursor is not None:
            params.append(int(cursor))
            conditions.append(f"sequence_no < ${len(params)}")
        if aggregate_type is not None:
            params.append(aggregate_type)
            conditions.append(f"aggregate_type = ${len(params)}")
        if action is not None:
            params.append(action)
            conditions.append(f"action = ${len(params)}")
        where_clause = " AND ".join(conditions)
        params.append(limit + 1)

        # F1(task-9458) — tenant_id is mandatory here (unlike append_event's
        # system-chain branch), so this is always a tenant_transaction.
        async with tenant_transaction(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                f"SELECT * FROM foundation_audit_event WHERE {where_clause} "  # noqa: S608
                f"ORDER BY sequence_no DESC LIMIT ${len(params)}",
                *params,
            )

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_row_to_event(row) for row in page_rows]
        next_cursor = str(page_rows[-1]["sequence_no"]) if has_more and page_rows else None
        return items, next_cursor

    async def get_latest_event(
        self, aggregate_type: str, aggregate_id: UUID, *, action: str
    ) -> AuditEvent | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM foundation_audit_event WHERE aggregate_type = $1 "
                "AND aggregate_id = $2 AND action = $3 "
                "ORDER BY sequence_no DESC LIMIT 1",
                aggregate_type,
                aggregate_id,
                action,
            )
        return _row_to_event(row) if row is not None else None

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        # F1(task-9458) — same system/tenant split as append_event.
        if tenant_id is not None:
            async with tenant_transaction(self._pool, tenant_id) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM foundation_audit_event WHERE tenant_id = $1 "
                    "ORDER BY sequence_no ASC",
                    tenant_id,
                )
        else:
            async with system_transaction(self._pool) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM foundation_audit_event WHERE tenant_id IS NULL "
                    "ORDER BY sequence_no ASC"
                )
        return [_row_to_event(row) for row in rows]
