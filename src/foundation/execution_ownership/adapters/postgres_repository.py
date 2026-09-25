"""Asyncpg implementation of ExecutionLeaseRepository.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§3.2, §5.1, §7. `acquire_or_renew_many` batch-expands the §5.1 conditional
UPSERT SQL via `UNNEST($1::bigint[])` to handle multiple execution_ids in
**one round-trip** (§7: "do not make individual round-trips per execution").
Asyncpg natively binds Python `list[int]` → `bigint[]` (§10 "confirm type
binding" — resolved by this implementation), so no `executemany` fallback
was needed. The one-round-trip-per-batch assertion is proven by
`tests/integration/foundation/execution_ownership/test_postgres_lease_repository.py`
counting `conn.fetch` calls.

If the same id appears twice in execution_ids, Postgres cannot update the
same row twice within a single statement and will fail the **entire batch**
with `CardinalityViolationError` ("ON CONFLICT DO UPDATE command cannot
affect row a second time"), leaving all executions in that tick un-ticked.
Deduplication preserves insertion order before binding (reproduced against
real DB in QA task-1143). Non-existent execution_ids (FK violations) are
intentionally not filtered out — the §5.1 SQL is used as-is, and the
contract is that the caller (EO-03 `list_candidates`) passes only ids read
from `strategy_executions`.
"""
from __future__ import annotations

import asyncpg


class PostgresExecutionLeaseRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        unique_ids = list(dict.fromkeys(execution_ids))
        if not unique_ids:
            return set()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                INSERT INTO execution_leases
                    (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
                SELECT eid, $2, 0, now(), now() + $3 * interval '1 second'
                FROM UNNEST($1::bigint[]) AS eid
                ON CONFLICT (execution_id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    heartbeat_at = now(),
                    expires_at = EXCLUDED.expires_at,
                    fencing_token = CASE
                        WHEN execution_leases.owner_id = EXCLUDED.owner_id
                            THEN execution_leases.fencing_token
                        ELSE execution_leases.fencing_token + 1
                    END
                WHERE execution_leases.owner_id = EXCLUDED.owner_id
                   OR execution_leases.expires_at < now()
                RETURNING execution_id
                """,
                unique_ids,
                owner_id,
                ttl_seconds,
            )
        return {row["execution_id"] for row in rows}

    async def release_all(self, owner_id: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM execution_leases WHERE owner_id = $1", owner_id
            )
        return int(result.split()[-1])
