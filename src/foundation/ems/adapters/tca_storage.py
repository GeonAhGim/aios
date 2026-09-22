"""EM-14 -- `tca_results` storage adapter, backed by `foundation_audit_event`.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`adapters/postgres_*.py` + migration for `tca_results`), #9 EM-14.

task-4013 (commit 379d6e31) implemented `application/compute_tca.py` +
`ports/tca_result_repository.py` (the `TcaResultRepository` Protocol) but
never an adapter, so `compute_tca` had no way to actually persist anything --
review task-4794 REJECTed it on that gap. This leaf closes it.

Decision (task-5277 note, avoiding CLAUDE.md "no duplicate context" +
frequent-mistake #9 migration-serialization risk): the spec table literally
says "adapters/postgres_*.py + migration" for `tca_results`, same as EM-6's
`route_decisions`. But EM-6 already earned its own WORM table because
`route_decisions` is queried heavily (routing dashboards, EM-18). `tca_results`
has no such read pattern yet, and this codebase already has a real, migrated,
WORM, hash-chained append-only store for exactly this shape of fact --
"record #N of aggregate X happened, here is its immutable payload" --
`foundation_audit_event` (FND-03, migration `4453afe74725`, reused as-is by
`foundation/performance/application/compute_statement.py`'s evidence trail).
Standing up a second bespoke `tca_results` table (+ migration, + RLS policy,
+ WORM trigger) to hold the same shape of fact is exactly the duplicate
context CLAUDE.md forbids, so this adapter stores TCA results as
`foundation_audit_event` rows instead: `aggregate_type="tca_result"`,
`aggregate_id=parent_id`, `aggregate_revision=revision`, `action=
"TCA_COMPUTED"`, tenant_id=None (system event -- TCA is not tenant-scoped
data, the parent order already carries whatever tenant context it needs).
If EM-18's TCA report page later needs indexed range queries this store
cannot serve efficiently, promoting to a dedicated table is a follow-up
leaf, not a blocker here (no new tables were free to invent for D2).

Reuses `PostgresAuditEventRepository.append_event_in` (evidence bounded
context's own adapter) for the actual write rather than re-deriving the hash
chain / advisory-lock serialization here -- same "reuse, don't reimplement"
posture `decomposition.py` documents for `hash_chain.canonical_json`.
`insert_or_get`'s idempotency (repeated calls for the same `(parent_id,
revision)` must not create a second row, DoD (b) / port docstring) is
enforced by holding the *same* advisory lock `append_event_in` takes
(`pg_advisory_xact_lock(hashtext('foundation_audit_event'), hashtext(
'system'))`, reentrant within one transaction) across an existence check and
the insert, in one transaction -- `foundation_audit_event` itself has no
unique constraint on `(aggregate_type, aggregate_id, aggregate_revision,
action)` (it is a general-purpose audit log, many actions can share an
aggregate_revision), so without holding that lock across both steps two
concurrent calls could both pass the existence check and each insert a row.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.foundation.ems.contracts.v1 import TcaResult
from src.foundation.ems.ports.tca_result_repository import TcaResultRecord
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import compute_payload_hash

AGGREGATE_TYPE = "tca_result"
ACTION = "TCA_COMPUTED"

_RESULT_FIELDS = (
    "arrival_bps",
    "vwap_bps",
    "impact_bps",
    "fees_bps",
    "opportunity_bps",
)


def _payload(result: TcaResult, computed_at: datetime) -> dict[str, Any]:
    return {
        **{field: str(getattr(result, field)) for field in _RESULT_FIELDS},
        "computed_at": computed_at.isoformat(),
    }


def _require_occurred_at(event: AuditEvent) -> datetime:
    """AuditEvent.occurred_at is Optional in the evidence model; a TCA record without a
    timestamp is a data defect, so fail closed rather than silently passing None."""
    if event.occurred_at is None:
        raise ValueError(f"audit event {event.id} has no occurred_at")
    return event.occurred_at


def _record_from_payload(
    *, tca_id: UUID, parent_id: UUID, revision: int, payload: dict[str, Any], created_at: datetime
) -> TcaResultRecord:
    values: dict[str, Any] = {field: Decimal(payload[field]) for field in _RESULT_FIELDS}
    result = TcaResult(**values)
    return TcaResultRecord(
        tca_id=tca_id,
        parent_id=parent_id,
        revision=revision,
        result=result,
        computed_at=datetime.fromisoformat(payload["computed_at"]),
        created_at=created_at,
    )


class PostgresTcaResultRepository:
    """`TcaResultRepository` implementation over `foundation_audit_event`
    (see module docstring for why there is no dedicated `tca_results`
    table)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._audit_repo = PostgresAuditEventRepository(pool)

    async def insert_or_get(
        self,
        *,
        parent_id: UUID,
        revision: int,
        result: TcaResult,
        computed_at: datetime,
    ) -> TcaResultRecord:
        async with self._pool.acquire() as conn, conn.transaction():
            # Same lock key as PostgresAuditEventRepository.append_event_in
            # (system-scope chain) -- reentrant within this transaction, so
            # calling append_event_in below (which re-acquires it) does not
            # block on itself. Held across the existence check *and* the
            # insert so the two are atomic against a concurrent insert_or_get
            # for the same (parent_id, revision).
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('foundation_audit_event'), "
                "hashtext('system'))"
            )
            existing = await conn.fetchrow(
                "SELECT id, payload, occurred_at FROM foundation_audit_event "
                "WHERE aggregate_type = $1 AND aggregate_id = $2 "
                "AND aggregate_revision = $3 AND action = $4 "
                "ORDER BY sequence_no ASC LIMIT 1",
                AGGREGATE_TYPE,
                parent_id,
                revision,
                ACTION,
            )
            if existing is not None:
                return _record_from_payload(
                    tca_id=existing["id"],
                    parent_id=parent_id,
                    revision=revision,
                    payload=json.loads(existing["payload"]),
                    created_at=existing["occurred_at"],
                )

            payload = _payload(result, computed_at)
            event = await self._audit_repo.append_event_in(
                conn,
                tenant_id=None,
                aggregate_type=AGGREGATE_TYPE,
                aggregate_id=parent_id,
                aggregate_revision=revision,
                action=ACTION,
                outcome=Outcome.SUCCESS,
                actor_subject_id=None,
                trace_id=uuid4(),
                payload_hash=compute_payload_hash(payload),
                payload=payload,
                classification=Classification.INTERNAL,
            )
            return _record_from_payload(
                tca_id=event.id,
                parent_id=parent_id,
                revision=revision,
                payload=payload,
                created_at=_require_occurred_at(event),
            )

    async def get_by_revision(self, parent_id: UUID, revision: int) -> TcaResultRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, payload, occurred_at FROM foundation_audit_event "
                "WHERE aggregate_type = $1 AND aggregate_id = $2 "
                "AND aggregate_revision = $3 AND action = $4 "
                "ORDER BY sequence_no ASC LIMIT 1",
                AGGREGATE_TYPE,
                parent_id,
                revision,
                ACTION,
            )
        if row is None:
            return None
        return _record_from_payload(
            tca_id=row["id"],
            parent_id=parent_id,
            revision=revision,
            payload=json.loads(row["payload"]),
            created_at=row["occurred_at"],
        )

    async def get_latest(self, parent_id: UUID) -> TcaResultRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, aggregate_revision, payload, occurred_at "
                "FROM foundation_audit_event "
                "WHERE aggregate_type = $1 AND aggregate_id = $2 AND action = $3 "
                "ORDER BY aggregate_revision DESC LIMIT 1",
                AGGREGATE_TYPE,
                parent_id,
                ACTION,
            )
        if row is None:
            return None
        return _record_from_payload(
            tca_id=row["id"],
            parent_id=parent_id,
            revision=row["aggregate_revision"],
            payload=json.loads(row["payload"]),
            created_at=row["occurred_at"],
        )
