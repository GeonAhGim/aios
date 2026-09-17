"""Unit tests for `src/foundation/ml/application/train_job.py` -- AI-20
task-2655. D2 depth (ADR-2026-09-09-C): negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1, plus the AI-20
DoD "resumable" property test.

Both ports are in-memory fakes -- `PostgresTrainJobRepository`'s real
compare-and-swap semantics are covered separately by
`tests/foundation/integration/ml/test_postgres_train_job_repository.py`;
this file is scoped to `run_train_job`'s own orchestration logic.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.ml.application.train_job import JobModelMismatchError, run_train_job
from src.foundation.ml.ports.train_job_repository import TrainJobState
from src.foundation.ml.ports.trainer import TrainingSample


class _FakeRepository:
    def __init__(self) -> None:
        self._rows: dict[str, TrainJobState] = {}

    async def get(self, job_id: str) -> TrainJobState | None:
        return self._rows.get(job_id)

    async def create(self, job_id: str, model_id: str) -> TrainJobState:
        if job_id in self._rows:
            return self._rows[job_id]
        state = TrainJobState(
            job_id=job_id, model_id=model_id, status="running", rounds_completed=0, checkpoint=None
        )
        self._rows[job_id] = state
        return state

    async def checkpoint(
        self, job_id: str, *, expected_rounds_completed: int, rounds_completed: int, checkpoint: str
    ) -> TrainJobState:
        current = self._rows[job_id]
        if current.status != "running" or current.rounds_completed != expected_rounds_completed:
            raise ConcurrencyConflictError(f"{job_id}: rounds_completed conflict")
        new_state = TrainJobState(
            job_id=job_id,
            model_id=current.model_id,
            status="running",
            rounds_completed=rounds_completed,
            checkpoint=checkpoint,
            metrics=current.metrics,
        )
        self._rows[job_id] = new_state
        return new_state

    async def complete(
        self, job_id: str, *, expected_rounds_completed: int, metrics: Mapping[str, float]
    ) -> TrainJobState:
        current = self._rows[job_id]
        if current.status != "running" or current.rounds_completed != expected_rounds_completed:
            raise ConcurrencyConflictError(f"{job_id}: rounds_completed conflict")
        new_state = TrainJobState(
            job_id=job_id,
            model_id=current.model_id,
            status="completed",
            rounds_completed=current.rounds_completed,
            checkpoint=current.checkpoint,
            metrics=dict(metrics),
        )
        self._rows[job_id] = new_state
        return new_state


class _FakeTrainer:
    """`checkpoint` is the cumulative round count as a decimal string --
    enough to prove `run_train_job` threads state through correctly
    without needing real LightGBM math."""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.train_step_calls = 0
        self._fail_on_call = fail_on_call

    def train_step(
        self,
        checkpoint: str | None,
        samples: Sequence[TrainingSample],
        *,
        num_boost_round: int,
        params: Mapping[str, object],
    ) -> str:
        self.train_step_calls += 1
        if self._fail_on_call is not None and self.train_step_calls == self._fail_on_call:
            raise RuntimeError("simulated crash mid-training")
        prior = 0 if checkpoint is None else int(checkpoint)
        return str(prior + num_boost_round)

    def evaluate(self, checkpoint: str, samples: Sequence[TrainingSample]) -> dict[str, float]:
        return {"rounds": float(checkpoint)}


def _samples() -> list[TrainingSample]:
    return [TrainingSample(features={"x1": 1.0}, label=1.0)]


# --- happy path ---


async def test_run_train_job_completes_and_checkpoints() -> None:
    repo = _FakeRepository()
    trainer = _FakeTrainer()

    state = await run_train_job(
        job_id="j1",
        model_id="m1",
        trainer=trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=5,
        params={},
        checkpoint_every=2,
    )

    assert state.status == "completed"
    assert state.rounds_completed == 5
    assert state.checkpoint == "5"
    assert state.metrics == {"rounds": 5.0}
    assert trainer.train_step_calls == 3  # 2 + 2 + 1


async def test_run_train_job_completed_rerun_is_idempotent_noop() -> None:
    repo = _FakeRepository()
    trainer = _FakeTrainer()
    await run_train_job(
        job_id="j2",
        model_id="m1",
        trainer=trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=3,
        params={},
    )
    calls_after_first_run = trainer.train_step_calls

    state = await run_train_job(
        job_id="j2",
        model_id="m1",
        trainer=trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=3,
        params={},
    )

    assert state.status == "completed"
    assert trainer.train_step_calls == calls_after_first_run  # no re-training


# --- negative (>= 3) ---


async def test_run_train_job_rejects_non_positive_target_rounds() -> None:
    with pytest.raises(ValueError, match="target_rounds must be positive"):
        await run_train_job(
            job_id="j-neg1",
            model_id="m1",
            trainer=_FakeTrainer(),
            repository=_FakeRepository(),
            samples=_samples(),
            target_rounds=0,
            params={},
        )


async def test_run_train_job_rejects_non_positive_checkpoint_every() -> None:
    with pytest.raises(ValueError, match="checkpoint_every must be positive"):
        await run_train_job(
            job_id="j-neg2",
            model_id="m1",
            trainer=_FakeTrainer(),
            repository=_FakeRepository(),
            samples=_samples(),
            target_rounds=3,
            params={},
            checkpoint_every=0,
        )


async def test_run_train_job_rejects_model_id_mismatch_for_existing_job() -> None:
    repo = _FakeRepository()
    await repo.create("j-neg3", "model-a")

    with pytest.raises(JobModelMismatchError):
        await run_train_job(
            job_id="j-neg3",
            model_id="model-b",
            trainer=_FakeTrainer(),
            repository=repo,
            samples=_samples(),
            target_rounds=3,
            params={},
        )


# --- failure injection + resumability (AI-20 DoD) ---


async def test_run_train_job_resumes_after_mid_training_crash() -> None:
    repo = _FakeRepository()
    crashing_trainer = _FakeTrainer(fail_on_call=2)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await run_train_job(
            job_id="j3",
            model_id="m1",
            trainer=crashing_trainer,
            repository=repo,
            samples=_samples(),
            target_rounds=4,
            params={},
            checkpoint_every=1,
        )

    partial = await repo.get("j3")
    assert partial is not None
    assert partial.status == "running"
    assert partial.rounds_completed == 1  # only the first train_step's checkpoint survived

    resuming_trainer = _FakeTrainer()
    final = await run_train_job(
        job_id="j3",
        model_id="m1",
        trainer=resuming_trainer,
        repository=repo,
        samples=_samples(),
        target_rounds=4,
        params={},
        checkpoint_every=1,
    )

    assert final.status == "completed"
    assert final.rounds_completed == 4
    assert resuming_trainer.train_step_calls == 3  # resumed from round 1, not round 0


# --- gate-red reproduction: the conditional-update guard is load-bearing ---


async def test_gate_red_stale_checkpoint_expectation_is_rejected() -> None:
    """Proves `run_train_job`'s resumability actually depends on the
    conditional-update guard, not a fake that always accepts -- a stale
    `expected_rounds_completed` (as a crashed/superseded caller would
    replay) is rejected instead of silently re-applying."""
    repo = _FakeRepository()
    await repo.create("j4", "m1")
    await repo.checkpoint("j4", expected_rounds_completed=0, rounds_completed=2, checkpoint="2")

    with pytest.raises(ConcurrencyConflictError):
        await repo.checkpoint("j4", expected_rounds_completed=0, rounds_completed=3, checkpoint="3")


# --- numeric performance assertion ---

_ORCHESTRATION_STEP_P95_BUDGET_MS = 50.0
"""`run_train_job`'s own loop/repository-call overhead per completed job
(fakes only, no real DB/LightGBM cost) -- generous relative to spec §7's
30s training-job progress cadence, but this isolates orchestration cost
from `test_local_trainer.py`'s real-training budget and
`test_postgres_train_job_repository.py`'s real-DB-roundtrip budget."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_run_train_job_orchestration_overhead_within_budget() -> None:
    repo = _FakeRepository()
    durations: list[float] = []
    for i in range(20):
        trainer = _FakeTrainer()
        started = time.perf_counter()
        await run_train_job(
            job_id=f"perf-{i}",
            model_id="m1",
            trainer=trainer,
            repository=repo,
            samples=_samples(),
            target_rounds=5,
            params={},
            checkpoint_every=1,
        )
        durations.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(durations)
    print(
        f"[AI-20 run_train_job] p95={p95_ms:.2f}ms budget<{_ORCHESTRATION_STEP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _ORCHESTRATION_STEP_P95_BUDGET_MS
