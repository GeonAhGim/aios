"""AI-20 -- LightGBM-backed local trainer + inference adapter.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`adapters/{...,local_trainer}.py`: "local training (LightGBM/torch,
after license check)"), §9 AI-20 DoD ("resumable").
`docs/design/AI_DEPENDENCIES_EVAL.md` §4 verdict: LightGBM MIT, no
conditions -- torch is dropped for this leaf (conditional verdict,
`THIRD_PARTY_NOTICES.md` obligation, and not even installed in this
venv); LightGBM alone satisfies the "LightGBM/torch" either/or in §2.5.

`LocalTrainer` satisfies both `ports/trainer.py::TrainerPort`
(`train_step`, checkpoint = `Booster.model_to_string()`, an opaque string
no other module parses) and `ports/predictor.py::ModelPredictorPort`
(`predict`), exactly as that port's own docstring anticipates. The two
methods never share mutable state -- `train_step` is a pure function of
its arguments (no filesystem I/O), `predict` reads a previously
`save_artifact`d file. `application/register_model.py` is the only
caller that writes an artifact; `train_step`'s checkpoint travels through
`ports/train_job_repository.py` (Postgres) until the job completes, only
landing on disk once (content-addressed by `model_hash`, standard-105
idempotent write -- same discipline as `adapters/parquet_feature_store.py`,
minus the digest streaming since checkpoints are small text, not tick
data).

Determinism (`ports/trainer.py::TrainerPort.train_step` docstring):
`deterministic=True` + `force_row_wise=True` + `num_threads=1` in
`_BASE_PARAMS` make LightGBM's own boosting deterministic given the same
data/params/round count, split across any number of `train_step` calls --
this is what lets `application/train_job.py` resume a job after a crash
and reach the same model a single uninterrupted run would.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile

import lightgbm as lgb
import numpy as np
import numpy.typing as npt

from src.foundation.ml.contracts.v1 import ModelCard
from src.foundation.ml.ports.trainer import TrainingSample

__all__ = ["ModelArtifactNotFoundError", "LocalTrainer"]

_BASE_PARAMS: dict[str, object] = {
    "objective": "regression",
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
}


class ModelArtifactNotFoundError(LookupError):
    """`card.model_hash` has no artifact under this trainer's root --
    either it was registered by a different trainer instance/root, or
    `application/register_model.py` never called `save_artifact` for it."""

    def __init__(self, model_id: str, version: str, model_hash: str) -> None:
        self.model_id = model_id
        self.version = version
        self.model_hash = model_hash
        super().__init__(
            f"model {model_id!r} version {version!r} (model_hash={model_hash!r}) "
            "has no artifact under this LocalTrainer's root"
        )


def _rows_and_labels(
    samples: Sequence[TrainingSample],
) -> tuple[list[str], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    if not samples:
        raise ValueError("samples must not be empty")
    feature_names = sorted(samples[0].features)
    rows: list[list[float]] = []
    labels: list[float] = []
    for sample in samples:
        if sorted(sample.features) != feature_names:
            raise ValueError("every sample in one train_step call must share the same feature set")
        rows.append([sample.features[name] for name in feature_names])
        labels.append(sample.label)
    return feature_names, np.array(rows, dtype=np.float64), np.array(labels, dtype=np.float64)


class LocalTrainer:
    def __init__(self, artifact_root: Path) -> None:
        self._root = Path(artifact_root)

    def train_step(
        self,
        checkpoint: str | None,
        samples: Sequence[TrainingSample],
        *,
        num_boost_round: int,
        params: Mapping[str, object],
    ) -> str:
        if num_boost_round <= 0:
            raise ValueError("num_boost_round must be positive")
        feature_names, rows, labels = _rows_and_labels(samples)

        init_model: lgb.Booster | None = None
        if checkpoint is not None:
            init_model = lgb.Booster(model_str=checkpoint)
            if init_model.feature_name() != feature_names:
                raise ValueError(
                    "resumed train_step feature set does not match the checkpoint's "
                    f"(checkpoint={init_model.feature_name()!r}, samples={feature_names!r})"
                )

        dataset = lgb.Dataset(rows, label=labels, feature_name=feature_names, free_raw_data=False)
        booster = lgb.train(
            {**_BASE_PARAMS, **params},
            dataset,
            num_boost_round=num_boost_round,
            init_model=init_model,
        )
        return booster.model_to_string()

    def evaluate(self, checkpoint: str, samples: Sequence[TrainingSample]) -> dict[str, float]:
        feature_names, rows, labels = _rows_and_labels(samples)
        booster = lgb.Booster(model_str=checkpoint)
        if booster.feature_name() != feature_names:
            raise ValueError(
                "evaluate samples' feature set does not match the checkpoint's "
                f"(checkpoint={booster.feature_name()!r}, samples={feature_names!r})"
            )
        predictions = booster.predict(rows)
        mse = sum((p - y) ** 2 for p, y in zip(predictions, labels, strict=True)) / len(labels)
        return {"rmse": mse**0.5}

    def save_artifact(self, model_hash: str, checkpoint: str) -> Path:
        """Content-addressed idempotent write (standard-105): identical
        retries succeed, a conflicting retry for an already-published
        `model_hash` fails closed. `application/register_model.py` calls
        this once, after `ModelCard.model_hash` has already been computed
        from `checkpoint` -- the filename and content can never disagree
        by construction."""
        path = self._root / f"{model_hash}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = checkpoint.encode("utf-8")
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            stream.write(encoded)
            temporary = Path(stream.name)
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != encoded:
                    raise FileExistsError(
                        f"model artifact model_hash={model_hash!r} already exists "
                        "with different content"
                    ) from None
            return path
        finally:
            temporary.unlink(missing_ok=True)

    def predict(self, card: ModelCard, features: Mapping[str, float]) -> float:
        path = self._root / f"{card.model_hash}.txt"
        try:
            model_str = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ModelArtifactNotFoundError(card.model_id, card.version, card.model_hash) from None
        booster = lgb.Booster(model_str=model_str)
        feature_names = booster.feature_name()
        missing = [name for name in feature_names if name not in features]
        if missing:
            raise ValueError(f"missing features for prediction: {missing}")
        row = np.array([[features[name] for name in feature_names]], dtype=np.float64)
        return float(booster.predict(row)[0])
