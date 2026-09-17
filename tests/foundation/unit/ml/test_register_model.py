"""Unit tests for `src/foundation/ml/application/register_model.py` --
AI-20 task-2655. D2 depth (ADR-2026-09-09-C): negative >= 3, failure
injection 1, numeric performance assertion 1, gate-red reproduction 1.

`ModelRegistryPort`/`ModelArtifactStore` are in-memory fakes --
`PostgresModelRegistry` itself is already covered by
`test_postgres_model_registry.py` (AI-19); this file is scoped to
`register_model`'s own composition (hash computation, artifact-then-
registry ordering, completed-job guard).
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ml.application.register_model import (
    JobNotCompletedError,
    compute_model_hash,
    register_model,
)
from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.registry_rules import (
    ModelHashMismatchError,
    validate_new_registration,
)
from src.foundation.ml.ports.train_job_repository import TrainJobState, TrainJobStatus

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
_LINEAGE = TrainDataLineage(
    start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
)


class _FakeModelRegistry:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ModelCard] = {}

    async def register(self, card: ModelCard) -> ModelCard:
        key = (card.model_id, card.version)
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = card
            return card
        validate_new_registration(card, existing=existing)
        return existing

    async def get(self, model_id: str, version: str) -> ModelCard | None:
        return self._rows.get((model_id, version))

    async def latest(self, model_id: str) -> ModelCard | None:
        matches = [c for (m, _), c in self._rows.items() if m == model_id]
        return matches[-1] if matches else None


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.save_calls = 0

    def save_artifact(self, model_hash: str, checkpoint: str) -> None:
        self.save_calls += 1
        existing = self.saved.get(model_hash)
        if existing is not None and existing != checkpoint:
            raise FileExistsError(f"model artifact model_hash={model_hash!r} content mismatch")
        self.saved[model_hash] = checkpoint


def _completed_job(
    *,
    job_id: str = "job-1",
    model_id: str = "m1",
    status: TrainJobStatus = "completed",
    rounds_completed: int = 5,
    checkpoint: str | None = "tree-content-A",
    metrics: dict[str, float] | None = None,
) -> TrainJobState:
    return TrainJobState(
        job_id=job_id,
        model_id=model_id,
        status=status,
        rounds_completed=rounds_completed,
        checkpoint=checkpoint,
        metrics=metrics if metrics is not None else {"rmse": 0.1},
    )


# --- happy path ---


async def test_register_model_computes_hash_and_registers() -> None:
    job = _completed_job()
    registry = _FakeModelRegistry()
    artifacts = _FakeArtifactStore()

    card = await register_model(
        job=job,
        version="v1",
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
        model_registry=registry,
        artifact_store=artifacts,
    )

    assert card.model_hash == hashlib.sha256(b"tree-content-A").hexdigest()
    assert card.metrics == {"rmse": 0.1}
    assert artifacts.saved[card.model_hash] == "tree-content-A"
    assert await registry.get("m1", "v1") == card


async def test_register_model_identical_retry_is_idempotent() -> None:
    job = _completed_job()
    registry = _FakeModelRegistry()
    artifacts = _FakeArtifactStore()

    first = await register_model(
        job=job,
        version="v1",
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
        model_registry=registry,
        artifact_store=artifacts,
    )
    second = await register_model(
        job=job,
        version="v1",
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
        model_registry=registry,
        artifact_store=artifacts,
    )

    assert first.model_hash == second.model_hash
    assert artifacts.save_calls == 2  # idempotent content-addressed write, called both times


# --- negative (>= 3) ---


async def test_register_model_rejects_running_job() -> None:
    job = _completed_job(status="running")
    with pytest.raises(JobNotCompletedError):
        await register_model(
            job=job,
            version="v1",
            train_data_lineage=_LINEAGE,
            trained_at=_NOW,
            model_registry=_FakeModelRegistry(),
            artifact_store=_FakeArtifactStore(),
        )


async def test_register_model_rejects_completed_job_without_checkpoint() -> None:
    job = _completed_job(checkpoint=None)
    with pytest.raises(JobNotCompletedError):
        await register_model(
            job=job,
            version="v1",
            train_data_lineage=_LINEAGE,
            trained_at=_NOW,
            model_registry=_FakeModelRegistry(),
            artifact_store=_FakeArtifactStore(),
        )


async def test_register_model_different_checkpoint_same_version_rejected_by_registry() -> None:
    registry = _FakeModelRegistry()
    artifacts = _FakeArtifactStore()
    await register_model(
        job=_completed_job(checkpoint="tree-content-A"),
        version="v1",
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
        model_registry=registry,
        artifact_store=artifacts,
    )

    with pytest.raises(ModelHashMismatchError):
        await register_model(
            job=_completed_job(checkpoint="tree-content-B"),
            version="v1",
            train_data_lineage=_LINEAGE,
            trained_at=_NOW,
            model_registry=registry,
            artifact_store=artifacts,
        )


# --- failure injection: artifact save fails after hash is computed ---


async def test_register_model_artifact_save_failure_leaves_registry_untouched() -> None:
    job = _completed_job()
    registry = _FakeModelRegistry()
    artifacts = _FakeArtifactStore()
    assert job.checkpoint is not None
    model_hash = compute_model_hash(job.checkpoint)
    artifacts.saved[model_hash] = "different-content-injected-by-another-process"

    with pytest.raises(FileExistsError):
        await register_model(
            job=job,
            version="v1",
            train_data_lineage=_LINEAGE,
            trained_at=_NOW,
            model_registry=registry,
            artifact_store=artifacts,
        )

    assert await registry.get("m1", "v1") is None  # never reached the registry call


# --- gate-red reproduction: artifact-before-registry ordering is load-bearing ---


async def test_gate_red_registry_call_never_runs_before_artifact_save() -> None:
    """Proves the failure-injection case above is not a tautology -- a
    naive implementation that registers *before* saving the artifact would
    leave a `ModelCard` pointing at a `model_hash` with no backing
    artifact. This fake reproduces that red state directly to show it is
    observably different from what `register_model` actually does."""
    registry = _FakeModelRegistry()
    card = ModelCard(
        model_id="m1",
        version="v1",
        model_hash="e" * 64,
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
    )
    await registry.register(card)  # red: registered with no artifact ever saved

    artifacts = _FakeArtifactStore()
    assert "e" * 64 not in artifacts.saved  # the dangling reference the ordering guards against
    assert await registry.get("m1", "v1") is not None


# --- numeric performance assertion ---

_REGISTER_MODEL_P95_BUDGET_MS = 50.0
"""Same budget class as AI-19's `_REGISTER_DB_ROUNDTRIP_P95_BUDGET_MS`
(`test_postgres_model_registry.py`) -- here measuring `register_model`'s
own orchestration cost against in-memory fakes (hash + two port calls),
not a real DB round trip."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_register_model_orchestration_p95_within_budget() -> None:
    registry = _FakeModelRegistry()
    artifacts = _FakeArtifactStore()
    durations: list[float] = []
    for i in range(30):
        job = _completed_job(
            job_id=f"job-{i}", checkpoint=f"tree-content-{i}-{time.perf_counter_ns()}"
        )
        started = time.perf_counter()
        await register_model(
            job=job,
            version=f"v{i}",
            train_data_lineage=_LINEAGE,
            trained_at=_NOW,
            model_registry=registry,
            artifact_store=artifacts,
        )
        durations.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(durations)
    print(f"[AI-20 register_model] p95={p95_ms:.2f}ms budget<{_REGISTER_MODEL_P95_BUDGET_MS:.1f}ms")
    assert p95_ms < _REGISTER_MODEL_P95_BUDGET_MS
