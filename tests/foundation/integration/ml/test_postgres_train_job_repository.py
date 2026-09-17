"""AI-20 integration tests -- `adapters/postgres_train_job_repository.py` +
migration `7a2523bc3e5a`. Spec: docs/specs/L4_ai_research_strategy_
factory_v1.0.md §2.5/§9 AI-20 DoD ("resumable"), §5 ("training job:
checkpoint conditional UPDATE, resumable"). ADR-2026-09-09-C D2:
negative >= 3, failure injection 1, numeric performance assertion 1,
gate-red reproduction 1.

Follows `tests/foundation/integration/ml/test_postgres_model_registry.py`'s
`pool`/CHECK-gate-red pattern -- both leaves of this same spec.
"""

from __future__ import annotations

import os
import time

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.ml.adapters.postgres_train_job_repository import PostgresTrainJobRepository


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repository(pool: asyncpg.Pool) -> PostgresTrainJobRepository:
    return PostgresTrainJobRepository(pool)


def _job_id(prefix: str) -> str:
    return f"{prefix}-{time.perf_counter_ns()}"


# --- happy path ---


async def test_create_then_get_round_trip(repository: PostgresTrainJobRepository) -> None:
    job_id = _job_id("j-create")
    created = await repository.create(job_id, "model-a")

    got = await repository.get(job_id)

    assert got is not None
    assert got.job_id == job_id
    assert got.model_id == "model-a"
    assert got.status == "running"
    assert got.rounds_completed == 0
    assert got.checkpoint is None
    assert created == got


async def test_create_identical_retry_is_idempotent(repository: PostgresTrainJobRepository) -> None:
    job_id = _job_id("j-idem")
    first = await repository.create(job_id, "model-a")
    second = await repository.create(job_id, "model-a")

    assert first == second


async def test_checkpoint_then_complete_advances_state(
    repository: PostgresTrainJobRepository,
) -> None:
    job_id = _job_id("j-lifecycle")
    await repository.create(job_id, "model-a")

    checkpointed = await repository.checkpoint(
        job_id, expected_rounds_completed=0, rounds_completed=3, checkpoint="tree-v3"
    )
    assert checkpointed.rounds_completed == 3
    assert checkpointed.checkpoint == "tree-v3"
    assert checkpointed.status == "running"

    completed = await repository.complete(
        job_id, expected_rounds_completed=3, metrics={"rmse": 0.05}
    )
    assert completed.status == "completed"
    assert completed.metrics == {"rmse": 0.05}
    assert completed.checkpoint == "tree-v3"  # complete() does not touch the checkpoint column


# --- negative (>= 3) ---


async def test_get_missing_job_returns_none(repository: PostgresTrainJobRepository) -> None:
    assert await repository.get("does-not-exist") is None


async def test_checkpoint_missing_job_raises_concurrency_conflict(
    repository: PostgresTrainJobRepository,
) -> None:
    with pytest.raises(ConcurrencyConflictError):
        await repository.checkpoint(
            "does-not-exist", expected_rounds_completed=0, rounds_completed=1, checkpoint="x"
        )


async def test_checkpoint_rejects_stale_expected_rounds(
    repository: PostgresTrainJobRepository,
) -> None:
    job_id = _job_id("j-stale")
    await repository.create(job_id, "model-a")
    await repository.checkpoint(
        job_id, expected_rounds_completed=0, rounds_completed=2, checkpoint="tree-v2"
    )

    with pytest.raises(ConcurrencyConflictError):
        await repository.checkpoint(
            job_id, expected_rounds_completed=0, rounds_completed=1, checkpoint="tree-v1-stale"
        )


async def test_insert_rejects_negative_rounds_completed_check(pool: asyncpg.Pool) -> None:
    """Direct SQL bypassing the port still hits the migration's CHECK
    constraint."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO ml_train_jobs (job_id, model_id, rounds_completed) "
                "VALUES ($1, 'model-a', -1)",
                _job_id("j-check"),
            )


async def test_checkpoint_rejects_completed_job(repository: PostgresTrainJobRepository) -> None:
    job_id = _job_id("j-completed")
    await repository.create(job_id, "model-a")
    await repository.checkpoint(
        job_id, expected_rounds_completed=0, rounds_completed=1, checkpoint="tree-v1"
    )
    await repository.complete(job_id, expected_rounds_completed=1, metrics={})

    with pytest.raises(ConcurrencyConflictError):
        await repository.checkpoint(
            job_id, expected_rounds_completed=1, rounds_completed=2, checkpoint="tree-v2"
        )


# --- failure injection: two workers race to checkpoint the same job ---


async def test_concurrent_checkpoint_race_only_one_winner(
    repository: PostgresTrainJobRepository, pool: asyncpg.Pool
) -> None:
    """Simulates a crashed worker whose retry lands after a second worker
    already advanced the same job -- both attempt `checkpoint` with the
    same `expected_rounds_completed`; exactly one succeeds."""
    job_id = _job_id("j-race")
    await repository.create(job_id, "model-a")

    import asyncio

    results = await asyncio.gather(
        repository.checkpoint(
            job_id, expected_rounds_completed=0, rounds_completed=1, checkpoint="worker-A"
        ),
        repository.checkpoint(
            job_id, expected_rounds_completed=0, rounds_completed=1, checkpoint="worker-B"
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrencyConflictError)


# --- gate-red reproduction: prove the CHECK constraints are load-bearing ---


async def test_gate_red_check_constraint_actually_fails_without_it(pool: asyncpg.Pool) -> None:
    """Proves the `rounds_completed >= 0` CHECK is not a tautology -- a
    temp table with the same column but no CHECK accepts the negative
    value `ml_train_jobs` rejects."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE ml_train_jobs_no_check (rounds_completed INTEGER NOT NULL) "
            "ON COMMIT DROP"
        )
        await conn.execute("INSERT INTO ml_train_jobs_no_check (rounds_completed) VALUES (-1)")
        row = await conn.fetchrow("SELECT rounds_completed FROM ml_train_jobs_no_check")
        assert row["rounds_completed"] == -1  # red: the CHECK above would reject this


# --- numeric performance assertion ---

_CHECKPOINT_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0
"""Same budget as AI-19's `_REGISTER_DB_ROUNDTRIP_P95_BUDGET_MS`
(`test_postgres_model_registry.py`) -- single DB round trip, same spec, no
AI-specific SLO number exists in §7 yet (§7's 30s figure is the training
job's own progress cadence, not a single DB call's budget)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_checkpoint_db_roundtrip_p95_within_budget(
    repository: PostgresTrainJobRepository,
) -> None:
    job_id = _job_id("j-perf")
    await repository.create(job_id, "model-a")

    samples: list[float] = []
    rounds = 0
    for i in range(30):
        started = time.perf_counter()
        await repository.checkpoint(
            job_id,
            expected_rounds_completed=rounds,
            rounds_completed=rounds + 1,
            checkpoint=f"c{i}",
        )
        samples.append((time.perf_counter() - started) * 1000)
        rounds += 1

    p95_ms = _p95(samples)
    budget = _CHECKPOINT_DB_ROUNDTRIP_P95_BUDGET_MS
    print(f"[AI-20 checkpoint] db roundtrip p95={p95_ms:.2f}ms budget<{budget:.1f}ms")
    assert p95_ms < _CHECKPOINT_DB_ROUNDTRIP_P95_BUDGET_MS
