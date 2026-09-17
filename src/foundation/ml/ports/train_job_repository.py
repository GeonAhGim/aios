"""AI-20 -- TrainJobRepositoryPort: checkpoint persistence port for
`application/train_job.py`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`application/{train_job,register_model,serve_signal}.py`: "training job
(checkpoint, resumable)"), §5 ("training job: checkpoint conditional
UPDATE, resumable"), §9 AI-20 DoD ("resumable").

Application code depends only on this `Protocol`, never on the concrete
asyncpg adapter in `adapters/postgres_train_job_repository.py` (standard
71 §4, same convention as `ports/model_registry.py`, AI-19).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

__all__ = ["TrainJobStatus", "TrainJobState", "TrainJobRepositoryPort"]

TrainJobStatus = Literal["running", "completed"]


@dataclass(frozen=True)
class TrainJobState:
    job_id: str
    model_id: str
    status: TrainJobStatus
    rounds_completed: int
    checkpoint: str | None
    metrics: Mapping[str, float] = field(default_factory=dict)


class TrainJobRepositoryPort(Protocol):
    async def get(self, job_id: str) -> TrainJobState | None: ...

    async def create(self, job_id: str, model_id: str) -> TrainJobState:
        """Idempotent (standard-105): if `job_id` already exists, returns
        the existing row untouched -- `rounds_completed`/`checkpoint` are
        never reset. A caller may safely re-submit the same train request
        after a crash without losing progress."""
        ...

    async def checkpoint(
        self,
        job_id: str,
        *,
        expected_rounds_completed: int,
        rounds_completed: int,
        checkpoint: str,
    ) -> TrainJobState:
        """Conditional UPDATE (standard-105): only advances the row if its
        current `rounds_completed` still equals `expected_rounds_completed`
        and `status='running'`. Raises `src.core.db.conditional_write.
        ConcurrencyConflictError` otherwise -- two workers stepping the same
        `job_id` concurrently, or a caller resuming a job that already
        finished, both fail closed instead of silently clobbering
        progress."""
        ...

    async def complete(
        self,
        job_id: str,
        *,
        expected_rounds_completed: int,
        metrics: Mapping[str, float],
    ) -> TrainJobState:
        """Conditional UPDATE to `status='completed'`, same guard as
        `checkpoint`. `metrics` at this point are final (evaluated against
        the finished checkpoint)."""
        ...
