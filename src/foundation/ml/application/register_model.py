"""AI-20 -- register_model: turn a completed `train_job` into a
registered `ModelCard` (the "integration" the AI-20 DoD asks for --
`local_trainer` + `train_job` + `register_model` wired end to end).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`application/{train_job,register_model,serve_signal}.py`), §9 AI-20 DoD
("integration").

`model_hash` is computed here, from the completed job's own checkpoint
(sha256 hex digest, the same shape `contracts/v1.py::ModelCard.model_hash`
validates) -- never trusted from the caller, so two different completed
checkpoints can never collide onto the same registered hash by caller
error. The artifact is saved *before* the registry call: if the process
dies between the two, the next retry re-saves the same content
(`save_artifact` is idempotent by content, `ports/model_registry.py::
ModelRegistryPort.register` is idempotent by `(model_id, version)` +
matching hash) -- a `model_hash` can therefore appear in the registry only
if its artifact was durably written first, never the other way round.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.ports.model_registry import ModelRegistryPort
from src.foundation.ml.ports.train_job_repository import TrainJobState

__all__ = ["ModelArtifactStore", "JobNotCompletedError", "register_model"]


class ModelArtifactStore(Protocol):
    def save_artifact(self, model_hash: str, checkpoint: str) -> object:
        """Durably store `checkpoint` under `model_hash` (content-addressed,
        idempotent). `adapters/local_trainer.py::LocalTrainer` satisfies
        this structurally -- no separate port file, since this is the same
        object `serve_signal`'s `ModelPredictorPort` already needs (spec
        §2.5 lists only `{feature_store,model_registry,trainer}` as ports;
        artifact storage is `local_trainer`'s own adapter-internal
        concern)."""
        ...


class JobNotCompletedError(ValueError):
    """`register_model` was called with a job whose `status != "completed"`
    -- registering an in-progress job's checkpoint would promote a
    partially-trained model as if it were finished (fail-closed)."""

    def __init__(self, job_id: str, status: str) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__(f"train job {job_id!r} is not completed (status={status!r})")


def compute_model_hash(checkpoint: str) -> str:
    return hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()


async def register_model(
    *,
    job: TrainJobState,
    version: str,
    train_data_lineage: TrainDataLineage,
    trained_at: datetime,
    model_registry: ModelRegistryPort,
    artifact_store: ModelArtifactStore,
    drift_baseline: Mapping[str, tuple[float, ...]] | None = None,
) -> ModelCard:
    if job.status != "completed" or job.checkpoint is None:
        raise JobNotCompletedError(job.job_id, job.status)

    model_hash = compute_model_hash(job.checkpoint)
    card = ModelCard(
        model_id=job.model_id,
        version=version,
        model_hash=model_hash,
        train_data_lineage=train_data_lineage,
        trained_at=trained_at,
        metrics=dict(job.metrics),
        drift_baseline=dict(drift_baseline or {}),
    )
    artifact_store.save_artifact(model_hash, job.checkpoint)
    return await model_registry.register(card)
