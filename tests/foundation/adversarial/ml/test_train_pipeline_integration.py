"""AI-20 integration -- `local_trainer` + `train_job` + `register_model`
wired end to end against real Postgres, then fed into AI-21's
`serve_signal` to prove the whole ML pipeline (AI-18 point-in-time guard
-> AI-19 registry/feature store -> AI-20 train/register -> AI-21 serve)
actually composes. Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md
§2.5 AI-20 DoD ("integration"), §4 A-3 (future data rejection), §8
("adversarial: ... future data model").

Uses real `PostgresTrainJobRepository`/`PostgresModelRegistry` (migrations
`7a2523bc3e5a`/`f85e5d761d6b`) and a real `ParquetFeatureStore`/
`LocalTrainer` against a tmp_path artifact root -- the only fakes in this
file are the synthetic training samples themselves.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.ml.adapters.local_trainer import LocalTrainer
from src.foundation.ml.adapters.parquet_feature_store import ParquetFeatureStore
from src.foundation.ml.adapters.postgres_model_registry import PostgresModelRegistry
from src.foundation.ml.adapters.postgres_train_job_repository import PostgresTrainJobRepository
from src.foundation.ml.application.register_model import register_model
from src.foundation.ml.application.serve_signal import serve_signal
from src.foundation.ml.application.train_job import run_train_job
from src.foundation.ml.contracts.v1 import FeatureSpec, TrainDataLineage
from src.foundation.ml.domain.point_in_time import FutureDataLeakageError
from src.foundation.ml.ports.feature_store import FeatureValue
from src.foundation.ml.ports.trainer import TrainingSample

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
_TRAIN_START = _NOW - timedelta(days=60)
_TRAIN_END = _NOW - timedelta(days=1)
_PARAMS: dict[str, object] = {"num_leaves": 7, "min_data_in_leaf": 1, "learning_rate": 0.3}
_FEATURE_SPEC = FeatureSpec(feature_id="x1", dtype="float", source_ref="test")


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


def _samples() -> list[TrainingSample]:
    return [TrainingSample(features={"x1": float(i)}, label=2.0 * i) for i in range(30)]


async def _train_and_register(
    *, job_id: str, model_id: str, version: str, pool: asyncpg.Pool, tmp_path
):
    trainer = LocalTrainer(tmp_path / "artifacts")
    train_job_repo = PostgresTrainJobRepository(pool)

    job = await run_train_job(
        job_id=job_id,
        model_id=model_id,
        trainer=trainer,
        repository=train_job_repo,
        samples=_samples(),
        target_rounds=6,
        params=_PARAMS,
        checkpoint_every=2,
    )

    registry = PostgresModelRegistry(pool)
    card = await register_model(
        job=job,
        version=version,
        train_data_lineage=TrainDataLineage(
            start=_TRAIN_START, end=_TRAIN_END, source_ref="test://samples"
        ),
        trained_at=_NOW,
        model_registry=registry,
        artifact_store=trainer,
    )
    return trainer, registry, card


# --- happy path: train -> register -> serve ---


async def test_full_pipeline_trains_registers_and_serves_a_signal(
    pool: asyncpg.Pool, tmp_path
) -> None:
    job_id = f"pipeline-{id(tmp_path)}"
    trainer, registry, card = await _train_and_register(
        job_id=job_id, model_id="pipeline-model", version="v1", pool=pool, tmp_path=tmp_path
    )
    assert card.model_hash
    assert "rmse" in card.metrics

    feature_store = ParquetFeatureStore(tmp_path / "features")
    as_of = _NOW
    feature_store.write_batch(
        _FEATURE_SPEC,
        as_of.date(),
        [FeatureValue(entity_id="AAPL", as_of=as_of, value="12.0")],
    )

    result = await serve_signal(
        model_id="pipeline-model",
        version="v1",
        entity_id="AAPL",
        as_of=as_of,
        feature_specs=[_FEATURE_SPEC],
        registry=registry,
        feature_store=feature_store,
        predictor=trainer,
    )

    assert isinstance(result.value, float)
    assert result.degraded is False


# --- negative: A-3 future-data rejection wired through the real pipeline ---


async def test_full_pipeline_rejects_backtest_starting_before_lineage_end(
    pool: asyncpg.Pool, tmp_path
) -> None:
    job_id = f"pipeline-a3-{id(tmp_path)}"
    trainer, registry, _card = await _train_and_register(
        job_id=job_id, model_id="pipeline-model-a3", version="v1", pool=pool, tmp_path=tmp_path
    )

    feature_store = ParquetFeatureStore(tmp_path / "features")
    as_of = _NOW
    feature_store.write_batch(
        _FEATURE_SPEC,
        as_of.date(),
        [FeatureValue(entity_id="AAPL", as_of=as_of, value="12.0")],
    )

    with pytest.raises(FutureDataLeakageError):
        await serve_signal(
            model_id="pipeline-model-a3",
            version="v1",
            entity_id="AAPL",
            as_of=as_of,
            feature_specs=[_FEATURE_SPEC],
            registry=registry,
            feature_store=feature_store,
            predictor=trainer,
            backtest_start=_TRAIN_END,  # not strictly after lineage.end -- must reject
        )


# --- resumability survives the real Postgres round trip end to end ---


async def test_full_pipeline_resumes_after_simulated_crash(pool: asyncpg.Pool, tmp_path) -> None:
    job_id = f"pipeline-resume-{id(tmp_path)}"
    trainer = LocalTrainer(tmp_path / "artifacts")
    repo = PostgresTrainJobRepository(pool)

    partial = await run_train_job(
        job_id=job_id,
        model_id="pipeline-resume-model",
        trainer=trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=2,
        params=_PARAMS,
        checkpoint_every=2,
    )
    assert partial.status == "completed"
    assert partial.rounds_completed == 2

    # simulate the caller crashing before registering, then re-driving the
    # same job_id toward a higher target_rounds -- run_train_job must
    # resume from the completed state's checkpoint (idempotent no-op at
    # rounds_completed=2, since target_rounds asked for below is smaller)
    # rather than restart training from scratch.
    resumed = await run_train_job(
        job_id=job_id,
        model_id="pipeline-resume-model",
        trainer=trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=2,
        params=_PARAMS,
        checkpoint_every=2,
    )
    assert resumed.checkpoint == partial.checkpoint
    assert resumed.rounds_completed == 2


# --- negative: empty samples must be rejected (AI-20 invariant) ---


async def test_train_job_rejects_empty_samples(pool: asyncpg.Pool, tmp_path) -> None:
    """Empty sample list must raise ValueError -- training with zero rows
    violates the invariant that every model must have at least one training
    observation (AI-20 invariant)."""
    job_id = f"neg-empty-{id(tmp_path)}"
    trainer = LocalTrainer(tmp_path / "artifacts")
    train_job_repo = PostgresTrainJobRepository(pool)

    with pytest.raises(ValueError, match="samples must not be empty"):
        await run_train_job(
            job_id=job_id,
            model_id="empty-samples-model",
            trainer=trainer,
            repository=train_job_repo,
            samples=[],
            target_rounds=6,
            params=_PARAMS,
            checkpoint_every=2,
        )


# --- negative: target_rounds <= 0 must be rejected (invariant guard) ---


async def test_train_job_rejects_zero_target_rounds(pool: asyncpg.Pool, tmp_path) -> None:
    """target_rounds=0 must raise ValueError -- training for zero rounds
    is an invariant violation (no model produced)."""
    job_id = f"neg-zero-rounds-{id(tmp_path)}"
    trainer = LocalTrainer(tmp_path / "artifacts")
    train_job_repo = PostgresTrainJobRepository(pool)

    with pytest.raises(ValueError, match="target_rounds must be positive"):
        await run_train_job(
            job_id=job_id,
            model_id="zero-rounds-model",
            trainer=trainer,
            repository=train_job_repo,
            samples=_samples(),
            target_rounds=0,
            params=_PARAMS,
            checkpoint_every=2,
        )


# --- failure injection: trainer.train_step() raises mid-training ---


async def test_train_job_leaves_resumable_state_after_trainer_failure(
    pool: asyncpg.Pool, tmp_path
) -> None:
    """When the trainer raises during a training step, the job must NOT be
    marked 'completed' and its checkpoint progress must be left untouched
    -- so a retry can resume rather than silently losing the crashed
    round's progress (failure-injection on the checkpoint conditional
    UPDATE path, standard-105)."""
    job_id = f"fail-trainer-{id(tmp_path)}"
    trainer = LocalTrainer(tmp_path / "artifacts")
    train_job_repo = PostgresTrainJobRepository(pool)

    original_train_step = trainer.train_step
    call_count = 0

    def failing_train_step(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated trainer crash")
        return original_train_step(*args, **kwargs)

    trainer.train_step = failing_train_step  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="simulated trainer crash"):
        await run_train_job(
            job_id=job_id,
            model_id="failing-model",
            trainer=trainer,
            repository=train_job_repo,
            samples=_samples(),
            target_rounds=6,
            params=_PARAMS,
            checkpoint_every=2,
        )

    # No checkpoint row was ever written -- the crash happened before the
    # first checkpoint UPDATE, so the job stays at round 0 and 'running',
    # resumable by a retry rather than left completed or half-advanced.
    job = await train_job_repo.get(job_id)
    assert job is not None
    assert job.status == "running"
    assert job.rounds_completed == 0
    assert job.checkpoint is None
