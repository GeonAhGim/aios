"""AI-10 -- `experiments` Postgres adapter.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4/§9 AI-10.

`experiments` is WORM (migration `c3f8a1d29b6e`, `worm_sql()` reuse from
`src/core/db/append_only.py` -- no bespoke trigger, same pattern as
`b76f4590b1b8_em6_route_decisions.py`). Because UPDATE/DELETE are denied to
everyone including the table owner, there is no upsert path here -- `append`
is a plain `INSERT`, and a duplicate `experiment_id` (a caller bug, ids are
UUIDs generated once per experiment) surfaces as a normal
`UniqueViolationError`, not silently swallowed.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind


def _row_to_experiment(row: asyncpg.Record) -> Experiment:
    return Experiment(
        experiment_id=row["experiment_id"],
        tenant_id=row["tenant_id"],
        reproducibility_key=row["reproducibility_key"],
        kind=ExperimentKind(row["kind"]),
        inputs_hash=row["inputs_hash"],
        metrics=json.loads(row["metrics"]),
        artifacts=tuple(row["artifacts"]),
        parent_id=row["parent_id"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


class PostgresExperimentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, experiment: Experiment) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO experiments "
                "(experiment_id, tenant_id, reproducibility_key, kind, inputs_hash, "
                " metrics, artifacts, parent_id, created_by, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10)",
                experiment.experiment_id,
                experiment.tenant_id,
                experiment.reproducibility_key,
                experiment.kind.value,
                experiment.inputs_hash,
                json.dumps(experiment.metrics),
                list(experiment.artifacts),
                experiment.parent_id,
                experiment.created_by,
                experiment.created_at,
            )

    async def get(self, tenant_id: UUID, experiment_id: UUID) -> Experiment | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM experiments WHERE tenant_id = $1 AND experiment_id = $2",
                tenant_id,
                experiment_id,
            )
        return _row_to_experiment(row) if row is not None else None

    async def find_by_reproducibility_key(
        self, tenant_id: UUID, reproducibility_key: str
    ) -> tuple[Experiment, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM experiments WHERE tenant_id = $1 AND reproducibility_key = $2 "
                "ORDER BY created_at",
                tenant_id,
                reproducibility_key,
            )
        return tuple(_row_to_experiment(row) for row in rows)
