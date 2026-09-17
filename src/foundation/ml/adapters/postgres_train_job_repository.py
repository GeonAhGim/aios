"""AI-20 -- `ml_train_jobs` Postgres adapter.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-20
("train_job (checkpoint)", "resumable"), §5 ("training job: checkpoint
conditional UPDATE, resumable").

`checkpoint`/`complete` both go through `src.core.db.conditional_write.
conditional_update` -- the shared standard-105 helper every new
`src/foundation/**` bounded context uses for conditional writes (that
module's own docstring), same convention `connections/adapters/
postgres_repository.py::update_connection_state` follows. `create` is a
plain `INSERT ... ON CONFLICT (job_id) DO NOTHING` idempotent insert (no
conditional-UPDATE needed for a first-time row).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.foundation.ml.ports.train_job_repository import TrainJobState

__all__ = ["PostgresTrainJobRepository"]

_TABLE = "ml_train_jobs"


def _row_to_state(row: asyncpg.Record) -> TrainJobState:
    return TrainJobState(
        job_id=row["job_id"],
        model_id=row["model_id"],
        status=row["status"],
        rounds_completed=row["rounds_completed"],
        checkpoint=row["checkpoint"],
        metrics=json.loads(row["metrics"]),
    )


class PostgresTrainJobRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, job_id: str) -> TrainJobState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM ml_train_jobs WHERE job_id = $1", job_id)
        return None if row is None else _row_to_state(row)

    async def create(self, job_id: str, model_id: str) -> TrainJobState:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO ml_train_jobs (job_id, model_id) VALUES ($1, $2) "
                "ON CONFLICT (job_id) DO NOTHING RETURNING *",
                job_id,
                model_id,
            )
            if row is not None:
                return _row_to_state(row)
            existing = await conn.fetchrow("SELECT * FROM ml_train_jobs WHERE job_id = $1", job_id)
        if existing is None:  # ON CONFLICT fired, so a row must exist
            raise AssertionError("ml_train_jobs row vanished between INSERT ON CONFLICT and SELECT")
        return _row_to_state(existing)

    async def checkpoint(
        self,
        job_id: str,
        *,
        expected_rounds_completed: int,
        rounds_completed: int,
        checkpoint: str,
    ) -> TrainJobState:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table=_TABLE,
                id_column="job_id",
                id_value=job_id,
                expected_state_column="rounds_completed",
                expected_state_value=expected_rounds_completed,
                set_values={
                    "rounds_completed": rounds_completed,
                    "checkpoint": checkpoint,
                    "updated_at": datetime.now(timezone.utc),
                },
                extra_conditions={"status": "running"},
            )
        return _row_to_state(row)

    async def complete(
        self,
        job_id: str,
        *,
        expected_rounds_completed: int,
        metrics: Mapping[str, float],
    ) -> TrainJobState:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table=_TABLE,
                id_column="job_id",
                id_value=job_id,
                expected_state_column="rounds_completed",
                expected_state_value=expected_rounds_completed,
                set_values={
                    "status": "completed",
                    "metrics": json.dumps(dict(metrics)),
                    "updated_at": datetime.now(timezone.utc),
                },
                extra_conditions={"status": "running"},
            )
        return _row_to_state(row)
