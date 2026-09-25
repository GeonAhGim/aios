"""AI-20 -- train_job: resumable, checkpointed training job.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`application/{train_job,register_model,serve_signal}.py`), §5
("training job: checkpoint conditional UPDATE, resumable"), §9 AI-20 DoD
("resumable"), §7 SLO ("training job progress updates every 30s").

`run_train_job` is idempotent on `job_id` (standard-105): calling it again
for a `job_id` that already reached `target_rounds` returns the completed
state without re-training; calling it again after a crash mid-training
resumes from `repository`'s last committed checkpoint instead of
restarting at round 0. Every `checkpoint_every` rounds is one conditional
UPDATE (§7's 30s progress cadence is a caller concern -- pick
`checkpoint_every` so each step's wall-clock time stays under that
budget).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.foundation.ml.ports.train_job_repository import TrainJobRepositoryPort, TrainJobState
from src.foundation.ml.ports.trainer import TrainerPort, TrainingSample

__all__ = ["JobModelMismatchError", "run_train_job"]


class JobModelMismatchError(ValueError):
    """`job_id` already exists but was created for a different `model_id`
    -- reusing a `job_id` across models would silently graft one model's
    checkpoint progress onto another's registration, so this is rejected
    fail-closed rather than resumed."""

    def __init__(self, job_id: str, existing_model_id: str, requested_model_id: str) -> None:
        self.job_id = job_id
        self.existing_model_id = existing_model_id
        self.requested_model_id = requested_model_id
        super().__init__(
            f"job {job_id!r} was created for model_id={existing_model_id!r}, "
            f"not model_id={requested_model_id!r}"
        )


async def run_train_job(
    *,
    job_id: str,
    model_id: str,
    trainer: TrainerPort,
    repository: TrainJobRepositoryPort,
    samples: Sequence[TrainingSample],
    target_rounds: int,
    params: Mapping[str, object],
    checkpoint_every: int = 1,
) -> TrainJobState:
    if target_rounds <= 0:
        raise ValueError("target_rounds must be positive")
    if checkpoint_every <= 0:
        raise ValueError("checkpoint_every must be positive")

    state = await repository.get(job_id)
    if state is None:
        state = await repository.create(job_id, model_id)
    if state.model_id != model_id:
        raise JobModelMismatchError(job_id, state.model_id, model_id)
    if state.status == "completed":
        return state

    checkpoint = state.checkpoint
    rounds_completed = state.rounds_completed
    while rounds_completed < target_rounds:
        step_rounds = min(checkpoint_every, target_rounds - rounds_completed)
        checkpoint = trainer.train_step(
            checkpoint, samples, num_boost_round=step_rounds, params=params
        )
        rounds_completed += step_rounds
        state = await repository.checkpoint(
            job_id,
            expected_rounds_completed=rounds_completed - step_rounds,
            rounds_completed=rounds_completed,
            checkpoint=checkpoint,
        )

    if checkpoint is None:  # target_rounds > 0 guarantees at least one train_step ran
        raise AssertionError("train_step loop ran zero iterations despite target_rounds > 0")
    metrics = trainer.evaluate(checkpoint, samples)
    return await repository.complete(
        job_id, expected_rounds_completed=rounds_completed, metrics=metrics
    )
