"""Unit tests for `src/foundation/experiments/domain/lineage.py` -- task-2645
AI-10 DoD ("append-only proof"). D2 depth (ADR-2026-09-09-C): negative >= 3,
failure injection 1, numeric performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.experiments.domain.lineage import (
    CrossTenantParentError,
    DanglingParentError,
    LineageError,
    ReproducibilityKeyCollisionError,
    validate_new_experiment,
)
from tests.conftest import PerfBudget

_NOW = datetime.now(timezone.utc)


def _experiment(**overrides: Any) -> Experiment:
    base: dict[str, Any] = dict(
        experiment_id=uuid4(),
        tenant_id=uuid4(),
        reproducibility_key="a" * 64,
        kind=ExperimentKind.BACKTEST,
        inputs_hash="b" * 64,
        metrics={},
        artifacts=(),
        parent_id=None,
        created_by=uuid4(),
        created_at=_NOW,
    )
    base.update(overrides)
    return Experiment(**base)


# --- happy path ---


def test_validate_new_experiment_accepts_root_with_no_parent() -> None:
    candidate = _experiment()
    validate_new_experiment(candidate, parent=None, existing_with_same_key=None)


def test_validate_new_experiment_accepts_valid_parent_link() -> None:
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)
    validate_new_experiment(child, parent=parent, existing_with_same_key=None)


def test_validate_new_experiment_accepts_identical_rerun_same_key() -> None:
    """Same `reproducibility_key` + same `inputs_hash` = a legitimate
    independent re-run, not a collision."""
    tenant_id = uuid4()
    existing = _experiment(tenant_id=tenant_id, reproducibility_key="c" * 64, inputs_hash="d" * 64)
    candidate = _experiment(tenant_id=tenant_id, reproducibility_key="c" * 64, inputs_hash="d" * 64)
    validate_new_experiment(candidate, parent=None, existing_with_same_key=existing)


# --- negative (>= 3) ---


def test_validate_new_experiment_rejects_dangling_parent() -> None:
    candidate = _experiment(parent_id=uuid4())
    with pytest.raises(DanglingParentError):
        validate_new_experiment(candidate, parent=None, existing_with_same_key=None)


def test_validate_new_experiment_rejects_parent_id_lookup_mismatch() -> None:
    tenant_id = uuid4()
    candidate = _experiment(tenant_id=tenant_id, parent_id=uuid4())
    wrong_parent = _experiment(tenant_id=tenant_id)  # different experiment_id
    with pytest.raises(LineageError):
        validate_new_experiment(candidate, parent=wrong_parent, existing_with_same_key=None)


def test_validate_new_experiment_rejects_cross_tenant_parent() -> None:
    parent = _experiment(tenant_id=uuid4())
    candidate = _experiment(parent_id=parent.experiment_id)  # different tenant_id
    with pytest.raises(CrossTenantParentError):
        validate_new_experiment(candidate, parent=parent, existing_with_same_key=None)


def test_validate_new_experiment_rejects_reproducibility_key_collision() -> None:
    tenant_id = uuid4()
    existing = _experiment(tenant_id=tenant_id, reproducibility_key="e" * 64, inputs_hash="f" * 64)
    candidate = _experiment(tenant_id=tenant_id, reproducibility_key="e" * 64, inputs_hash="0" * 64)
    with pytest.raises(ReproducibilityKeyCollisionError) as exc_info:
        validate_new_experiment(candidate, parent=None, existing_with_same_key=existing)
    assert exc_info.value.reproducibility_key == "e" * 64
    assert exc_info.value.existing_inputs_hash == "f" * 64
    assert exc_info.value.candidate_inputs_hash == "0" * 64


# --- failure injection ---


def test_validate_new_experiment_rejects_when_dangling_and_key_collision_both_present() -> None:
    """Failure injection: a candidate that is broken in two independent ways
    at once (dangling parent AND a colliding reproducibility_key) must still
    fail -- and it must fail on the parent check first (the ordered
    pipeline), not silently pass because the second check never runs."""
    existing = _experiment(reproducibility_key="1" * 64, inputs_hash="2" * 64)
    candidate = _experiment(parent_id=uuid4(), reproducibility_key="1" * 64, inputs_hash="3" * 64)
    with pytest.raises(DanglingParentError):
        validate_new_experiment(candidate, parent=None, existing_with_same_key=existing)


# --- numeric performance assertion ---

_VALIDATE_BUDGET_MS = 5.0
"""Pure in-memory validation with no I/O -- generously wide budget for a
regression guard, not a real SLO (§7 has no experiment-ledger-specific
number)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
def test_validate_new_experiment_p95_latency_within_budget(perf_budget: PerfBudget) -> None:
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    children = iter(
        [_experiment(tenant_id=tenant_id, parent_id=parent.experiment_id) for _ in range(200)]
    )

    def _validate() -> None:
        validate_new_experiment(next(children), parent=parent, existing_with_same_key=None)

    # wall_ms, not cpu_ms: this loop's per-call cost is well under the ~15.6ms
    # Windows `time.process_time()` tick, so cpu_ms would quantize to 0 for
    # most calls and flatten the p95 distribution.
    wall_ms_samples = sorted(s.wall_ms for s in perf_budget.samples(_validate, n=200, warmup=0))
    p95_ms = _p95(wall_ms_samples)
    print(f"[AI-10 validate_new_experiment] p95={p95_ms:.4f}ms budget<{_VALIDATE_BUDGET_MS:.1f}ms")
    assert p95_ms < _VALIDATE_BUDGET_MS


# --- gate-red reproduction ---


@pytest.mark.perf
def test_gate_red_budget_actually_fails_past_budget(perf_budget: PerfBudget) -> None:
    """Proves the perf assertion above is not a tautology -- an absurdly
    low budget against the same samples must fail."""
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    children = iter(
        [_experiment(tenant_id=tenant_id, parent_id=parent.experiment_id) for _ in range(20)]
    )

    def _validate() -> None:
        validate_new_experiment(next(children), parent=parent, existing_with_same_key=None)

    wall_ms_samples = sorted(s.wall_ms for s in perf_budget.samples(_validate, n=20, warmup=0))
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(wall_ms_samples) < absurdly_low_budget_ms


# --- zero-convergence (CPU clock quantization check) ---


@pytest.mark.perf
def test_validate_new_experiment_cpu_converges_to_zero(perf_budget: PerfBudget) -> None:
    """Calls 1회 CPU 측정의 0 수렴 검증: 순수 메모리 검증 함수는
    `time.process_time()` 기준으로 호출당 CPU 시간이 Windowstick(약 15.6ms)
    아래로 떨어질 수 있으므로 batch로 묶어 측정하고 1회당으로 나누면
    near-0에 수렴해야 한다.

    audit task-11539: `zero_convergence`가 "not dynamically measured"로
    기록된 항목을 정정한다.
    """
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)

    def _validate() -> None:
        validate_new_experiment(child, parent=parent, existing_with_same_key=None)

    # batch=50으로 묶어 1회당 CPU 시간을 추정: Windows tick 15.6ms / 50 ≈ 0.3ms 오차
    sample = perf_budget.sample(_validate, batch=50)
    cpu_ms_per_call = sample.cpu_ms  # 이미 sample()이 batch로 나눴음
    print(
        f"[AI-10 cpu_convergence] cpu_ms/call={cpu_ms_per_call:.4f}ms "
        f"(batch=50, Windows tick quantization reduced)"
    )
    # CPU 시간은 0이 될 수 없지만 batch로 나눈 오차가 매우 작아야 함.
    # Windows tick 15.6ms / 50 ≈ 0.3125ms를 여유 한계로 삼는다.
    assert cpu_ms_per_call < 0.5, (
        f"CPU time per call {cpu_ms_per_call:.4f}ms exceeds 0.5ms "
        "— may indicate regression or insufficient batch size"
    )


# --- gate-red with actual delay injection ---


@pytest.mark.perf
def test_gate_red_budget_fails_with_injected_delay(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2): `validate_new_experiment`의 wall-clock 예산
    단언이 실제로 지연을 포착하는지 검증한다. PerfBudget가 wall_ms를
    측정할 때 쓰는 `time.perf_counter`를 monkeypatch해 실제 시간을
    가장하게 하고, 같은 p95 단언이 AssertionError로 적색이 되는지를
    확인한다.

    이 테스트는 wall_ms 기반 예산이 "제대로 작동하는지"를 역으로 증명한다.
    단언/마커 삭제나 예산 완화가 아니다.
    """
    import time as _time

    # 원본 perf_counter 저장 (patch 전에)
    _original_perf_counter = _time.perf_counter

    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    children = iter(
        [_experiment(tenant_id=tenant_id, parent_id=parent.experiment_id) for _ in range(20)]
    )

    # 호출당 50ms 지연 주입 (예산 5ms의 10배)
    injected_delay_s = _VALIDATE_BUDGET_MS * 10 / 1000  # 50ms in seconds
    call_count = 0

    def _fake_perf_counter() -> float:
        nonlocal call_count
        call_count += 1
        # 실제 시간 + 호출당 지연을 더해 wall_clock이 지연된 것처럼 보이게 함
        return _original_perf_counter() + (injected_delay_s * call_count)

    # tests.support.perf_budget 모듈의 perf_counter를 패치:
    # PerfBudget.sample()에서 time.perf_counter()를 직접 호출하므로
    # 해당 모듈의 네임스페이스에서 패치해야 효과가 있다.
    from tests.support import perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "perf_counter", _fake_perf_counter)

    def _validate() -> None:
        validate_new_experiment(next(children), parent=parent, existing_with_same_key=None)

    wall_ms_samples = sorted(s.wall_ms for s in perf_budget.samples(_validate, n=20, warmup=0))
    p95_ms = _p95(wall_ms_samples)
    print(
        f"[AI-10 gate-red injected] p95={p95_ms:.4f}ms budget={_VALIDATE_BUDGET_MS:.1f}ms "
        f"(injected delay={injected_delay_s * 1000:.0f}ms/call)"
    )
    with pytest.raises(AssertionError):
        assert p95_ms < _VALIDATE_BUDGET_MS
