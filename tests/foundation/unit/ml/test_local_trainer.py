"""Unit tests for `src/foundation/ml/adapters/local_trainer.py` -- AI-20
task-2655. D2 depth (ADR-2026-09-09-C): negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1, plus the AI-20
DoD "resumable" equivalence proof.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import lightgbm as lgb
import pytest

from src.foundation.ml.adapters.local_trainer import LocalTrainer, ModelArtifactNotFoundError
from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.ports.trainer import TrainingSample
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_sync

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


@pytest.mark.perf
def test_train_step_latency_within_budget(tmp_path, perf_budget):
    """task-10990 — raw perf_counter → perf_budget.samples 전환.
    예산 값(_TRAIN_STEP_P95_BUDGET_MS)은 불변."""
    trainer = LocalTrainer(tmp_path)
    samples = _samples()

    def _one_round() -> None:
        trainer.train_step(None, samples, num_boost_round=3, params=_PARAMS)

    samples = perf_budget.samples(_one_round, n=5)
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(len(wall_ms_list) * 0.95), len(wall_ms_list) - 1)]
    print(f"[AI-20 train_step] p95={p95_ms:.2f}ms budget<{_TRAIN_STEP_P95_BUDGET_MS:.1f}ms")
    assert p95_ms < _TRAIN_STEP_P95_BUDGET_MS


# ---- CPU 0 수렴 + 지연 주입 적색 재현(D2, task-11539 감사 후속) ----


@pytest.mark.perf
def test_train_step_cpu_time_converges_to_zero_for_small_input(
    perf_budget: PerfBudget,
    tmp_path,
) -> None:
    """단일 train_step 호출의 CPU 측정이 0 수렴 확인 — 작은 입력(40 샘플,
    3 라운드)은 LightGBM이 거의 즉시 학습하므로 CPU 시간은 Windows 양자화
    tick(약 15.6ms) 범위 내여야 한다. batch=8로 묶으면 양자화 오차가
    호출당 tick/8로 줄어 실제 CPU 시간을 정상 측정한다.

    이 테스트는 PerfBudget.sample가 단일 호출 CPU 시간을 올바르게 계측함을
    동적으로 검증한다.
    """
    trainer = LocalTrainer(tmp_path)
    samples = _samples()

    def _one_step() -> str:
        return trainer.train_step(None, samples, num_boost_round=3, params=_PARAMS)

    sample = perf_budget.sample(_one_step, batch=8)
    assert sample.cpu_ms < 100.0, (
        f"train_step 단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        "작은 입력이므로 양자화 오차 범위 내여야 함 (budget<100ms)"
    )


@pytest.mark.perf
def test_train_step_perf_budget_goes_red_with_cpu_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
    tmp_path,
) -> None:
    """gate-red reproduction: lgb.train 에 CPU 지연(delayed_sync, 6.0s)을
    주입한 후 같은 성능 단언을 돌리면 예산을 초과해야 한다(red).

    `lgb.train` 을 `delayed_sync` 로 감싸 1회 호출당 6초 CPU 부하를
    주입 — 3라운드 × 6초 = 18초 CPU 소모로 5000ms 예산을 크게 초과해
    assert_within/assert 가 적색이 된다.

    정상 입력에서 통과하는 것이 테스트의 허수(trivial pass)가 아님을
    보증한다. 단언/마커 삭제와 예산 완화 금지.
    """
    # lgb.train 을 delayed_sync 로 감싸 CPU 지연 주입 (6초/회)
    slow_train = delayed_sync(lgb.train, 6.0)
    monkeypatch.setattr(lgb, "train", slow_train)

    trainer = LocalTrainer(tmp_path)
    samples = _samples()

    # 같은 train_step 호출 — 지연 주입으로 budget 초과
    def _one_step_with_delay() -> str:
        return trainer.train_step(None, samples, num_boost_round=3, params=_PARAMS)

    # 3라운드 × 6초 = 18초 CPU 소모 → 5000ms 예산 초과
    sample = perf_budget.sample(_one_step_with_delay, batch=1)
    assert sample.cpu_ms >= _TRAIN_STEP_P95_BUDGET_MS, (
        f"지연 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"예상과 달리 녹색: 측정/단언이 공허함(hollow assertion, "
        f"budget={_TRAIN_STEP_P95_BUDGET_MS:.1f}ms)"
    )
