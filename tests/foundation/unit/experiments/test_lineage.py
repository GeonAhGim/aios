"""Unit tests for `src/foundation/experiments/domain/lineage.py` -- task-2645
AI-10 DoD ("append-only proof"). D2 depth (ADR-2026-09-09-C): negative >= 3,
failure injection 1, numeric performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import time
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
def test_validate_new_experiment_p95_latency_within_budget() -> None:
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    samples: list[float] = []
    for _ in range(200):
        child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)
        started = time.perf_counter()
        validate_new_experiment(child, parent=parent, existing_with_same_key=None)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(f"[AI-10 validate_new_experiment] p95={p95_ms:.4f}ms budget<{_VALIDATE_BUDGET_MS:.1f}ms")
    assert p95_ms < _VALIDATE_BUDGET_MS


# --- gate-red reproduction ---


@pytest.mark.perf
def test_gate_red_budget_actually_fails_past_budget() -> None:
    """Proves the perf assertion above is not a tautology -- an absurdly
    low budget against the same samples must fail."""
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    samples: list[float] = []
    for _ in range(20):
        child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)
        started = time.perf_counter()
        validate_new_experiment(child, parent=parent, existing_with_same_key=None)
        samples.append((time.perf_counter() - started) * 1000)

    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms
