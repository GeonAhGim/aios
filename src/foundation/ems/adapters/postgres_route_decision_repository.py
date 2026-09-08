"""EM-6 -- `route_decisions` Postgres adapter.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2/§9 EM-6.

`route_decisions` is WORM (migration `b76f4590b1b8`, `worm_sql()` reuse --
no bespoke trigger here, decision item 2). Because UPDATE is denied even to
the table owner, idempotent insert cannot use `ON CONFLICT ... DO UPDATE`;
it uses the same `ON CONFLICT (order_id) DO NOTHING RETURNING *` + re-select
pattern as `foundation/connections/adapters/postgres_snapshot_mixin.py` --
the losing side of a race is not an error, it is a normal duplicate-attempt
outcome and gets back the row the winner inserted.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord


def _row_to_record(row: asyncpg.Record) -> RouteDecisionRecord:
    return RouteDecisionRecord(
        decision_id=row["decision_id"],
        order_id=row["order_id"],
        decision=RouteDecision(
            venue=row["venue"],
            reason_codes=list(row["reason_codes"]),
            expected_cost_bps=row["expected_cost_bps"],
        ),
        candidates_snapshot=json.loads(row["candidates_snapshot"]),
        score_snapshot=json.loads(row["score_snapshot"]),
        weights_snapshot=json.loads(row["weights_snapshot"]),
        decided_at=row["decided_at"],
        created_at=row["created_at"],
    )


class PostgresRouteDecisionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_or_get(
        self,
        *,
        order_id: UUID,
        decision: RouteDecision,
        candidates_snapshot: list[dict[str, Any]],
        score_snapshot: list[dict[str, Any]],
        weights_snapshot: dict[str, Any],
        decided_at: datetime,
    ) -> RouteDecisionRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO route_decisions "
                "(order_id, venue, reason_codes, expected_cost_bps, candidates_snapshot, "
                " score_snapshot, weights_snapshot, decided_at) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8) "
                "ON CONFLICT (order_id) DO NOTHING "
                "RETURNING *",
                order_id,
                decision.venue,
                list(decision.reason_codes),
                decision.expected_cost_bps,
                json.dumps(candidates_snapshot),
                json.dumps(score_snapshot),
                json.dumps(weights_snapshot),
                decided_at,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM route_decisions WHERE order_id = $1", order_id
                )
            assert row is not None  # ON CONFLICT DO NOTHING이 발동했다면 반드시 존재
        return _row_to_record(row)

    async def get_by_order_id(self, order_id: UUID) -> RouteDecisionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM route_decisions WHERE order_id = $1", order_id
            )
        return _row_to_record(row) if row is not None else None
