"""Unit tests for `src/foundation/ml/adapters/local_trainer.py` -- AI-20
task-2655. D2 depth (ADR-2026-09-09-C): negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1, plus the AI-20
DoD "resumable" equivalence proof.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import lightgbm as lgb
import pytest

from src.foundation.ml.adapters.local_trainer import LocalTrainer, ModelArtifactNotFoundError
from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.ports.trainer import TrainingSample

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
_PARAMS: dict[str, object] = {"num_leaves": 7, "min_data_in_leaf": 1, "learning_rate": 0.3}


def _samples() -> list[TrainingSample]:
    return [
        TrainingSample(features={"x1": float(i), "x2": float(i % 5)}, label=2.0 * i + (i % 5))
        for i in range(40)
    ]


def _card(model_hash: str) -> ModelCard:
    return ModelCard(
        model_id="m",
        version="v1",
        model_hash=model_hash,
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
        ),
        trained_at=_NOW,
    )


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


# --- resumability (AI-20 DoD) ---


def _strip_num_iterations_echo(model_str: str) -> str:
    """LightGBM's trailing param-echo block includes `[num_iterations: N]`
    -- the `num_boost_round` argument of the *last* `train_step` call, not
    the cumulative tree count -- so it legitimately differs between one
    five-round call and a resumed 3+2 sequence even when every tree is
    byte-identical. Stripped before comparing so the resumability
    assertion checks actual model content (every tree line), not this one
    incidental echoed parameter."""
    return "\n".join(
        line for line in model_str.splitlines() if not line.startswith("[num_iterations:")
    )


def test_resumed_training_matches_uninterrupted_training(tmp_path):
    trainer = LocalTrainer(tmp_path)
    samples = _samples()

    direct = trainer.train_step(None, samples, num_boost_round=5, params=_PARAMS)

    resumed = trainer.train_step(None, samples, num_boost_round=3, params=_PARAMS)
    resumed = trainer.train_step(resumed, samples, num_boost_round=2, params=_PARAMS)

    assert _strip_num_iterations_echo(direct) == _strip_num_iterations_echo(resumed)


def test_resumed_training_across_three_steps_still_matches(tmp_path):
    trainer = LocalTrainer(tmp_path)
    samples = _samples()

    direct = trainer.train_step(None, samples, num_boost_round=6, params=_PARAMS)

    resumed = None
    for _ in range(6):
        resumed = trainer.train_step(resumed, samples, num_boost_round=1, params=_PARAMS)

    assert _strip_num_iterations_echo(direct) == _strip_num_iterations_echo(resumed)


# --- happy path ---


def test_predict_after_resume_uses_full_round_count(tmp_path):
    trainer = LocalTrainer(tmp_path)
    samples = _samples()
    checkpoint = trainer.train_step(None, samples, num_boost_round=3, params=_PARAMS)
    checkpoint = trainer.train_step(checkpoint, samples, num_boost_round=3, params=_PARAMS)

    model_hash = "a" * 64
    trainer.save_artifact(model_hash, checkpoint)
    card = _card(model_hash)

    value = trainer.predict(card, {"x1": 10.0, "x2": 0.0})
    assert isinstance(value, float)


def test_save_artifact_identical_retry_is_idempotent(tmp_path):
    trainer = LocalTrainer(tmp_path)
    path1 = trainer.save_artifact("a" * 64, "checkpoint-text")
    path2 = trainer.save_artifact("a" * 64, "checkpoint-text")
    assert path1 == path2
    assert path1.read_text(encoding="utf-8") == "checkpoint-text"


# --- negative (>= 3) ---


def test_train_step_rejects_empty_samples(tmp_path):
    trainer = LocalTrainer(tmp_path)
    with pytest.raises(ValueError, match="samples must not be empty"):
        trainer.train_step(None, [], num_boost_round=1, params=_PARAMS)


def test_train_step_rejects_inconsistent_feature_sets(tmp_path):
    trainer = LocalTrainer(tmp_path)
    samples = [
        TrainingSample(features={"x1": 1.0}, label=1.0),
        TrainingSample(features={"x1": 2.0, "x2": 3.0}, label=2.0),
    ]
    with pytest.raises(ValueError, match="same feature set"):
        trainer.train_step(None, samples, num_boost_round=1, params=_PARAMS)


def test_train_step_rejects_resume_with_different_feature_set(tmp_path):
    trainer = LocalTrainer(tmp_path)
    checkpoint = trainer.train_step(None, _samples(), num_boost_round=1, params=_PARAMS)
    other_shape = [TrainingSample(features={"x1": 1.0}, label=1.0)]
    with pytest.raises(ValueError, match="does not match the checkpoint"):
        trainer.train_step(checkpoint, other_shape, num_boost_round=1, params=_PARAMS)


def test_predict_missing_artifact_raises(tmp_path):
    trainer = LocalTrainer(tmp_path)
    with pytest.raises(ModelArtifactNotFoundError):
        trainer.predict(_card("b" * 64), {"x1": 1.0, "x2": 1.0})


def test_predict_missing_required_feature_raises(tmp_path):
    trainer = LocalTrainer(tmp_path)
    checkpoint = trainer.train_step(None, _samples(), num_boost_round=2, params=_PARAMS)
    model_hash = "c" * 64
    trainer.save_artifact(model_hash, checkpoint)

    with pytest.raises(ValueError, match="missing features"):
        trainer.predict(_card(model_hash), {"x1": 1.0})


# --- failure injection: corrupted checkpoint fails closed ---


def test_train_step_with_corrupted_checkpoint_fails_closed(tmp_path):
    trainer = LocalTrainer(tmp_path)
    with pytest.raises(lgb.basic.LightGBMError):
        trainer.train_step(
            "not a valid lightgbm model string", _samples(), num_boost_round=1, params=_PARAMS
        )


# --- gate-red reproduction: content-addressed guard is load-bearing ---


def test_gate_red_content_addressed_guard_is_load_bearing(tmp_path):
    """Proves the `FileExistsError` below is not a tautology -- writing
    directly to the artifact path (bypassing `save_artifact`'s digest
    check) lets mismatched content land silently under the same
    `model_hash`."""
    trainer = LocalTrainer(tmp_path)
    model_hash = "d" * 64
    trainer.save_artifact(model_hash, "checkpoint-A")

    path = tmp_path / f"{model_hash}.txt"
    path.write_text("checkpoint-B", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "checkpoint-B"  # red: bypassed the guard

    path.write_text("checkpoint-A", encoding="utf-8")
    with pytest.raises(FileExistsError):
        trainer.save_artifact(model_hash, "checkpoint-B")


# --- numeric performance assertion ---

_TRAIN_STEP_P95_BUDGET_MS = 5000.0
"""spec §7 SLO: training job progress updates every 30s -- one
`train_step` call (the checkpoint cadence `application/train_job.py`
drives) must complete well within that window; this asserts 1/6 of it for
this file's small in-repo dataset."""


def test_train_step_latency_within_budget(tmp_path):
    trainer = LocalTrainer(tmp_path)
    samples = _samples()
    durations: list[float] = []
    checkpoint = None
    for _ in range(5):
        started = time.perf_counter()
        checkpoint = trainer.train_step(checkpoint, samples, num_boost_round=3, params=_PARAMS)
        durations.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(durations)
    print(f"[AI-20 train_step] p95={p95_ms:.2f}ms budget<{_TRAIN_STEP_P95_BUDGET_MS:.1f}ms")
    assert p95_ms < _TRAIN_STEP_P95_BUDGET_MS
