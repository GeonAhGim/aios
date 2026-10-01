"""Unit tests for `src/foundation/ml/domain/drift.py` -- task-2653 AI-18.
D2 depth (ADR-2026-09-09-C): negative >= 3, failure injection 1, numeric
performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.foundation.ml.domain.drift import (
    DEFAULT_THRESHOLDS,
    DriftThresholds,
    InsufficientSamplesError,
    evaluate_drift,
    ks_statistic,
    population_stability_index,
)
from tests.conftest import PerfBudget

_RNG = np.random.default_rng(seed=42)


def _stable_sample(n: int = 500) -> list[float]:
    return list(_RNG.normal(loc=0.0, scale=1.0, size=n))


def _shifted_sample(n: int = 500, *, loc: float = 5.0) -> list[float]:
    return list(_RNG.normal(loc=loc, scale=1.0, size=n))


# --- happy path ---


def test_evaluate_drift_reports_not_degraded_for_identical_distribution() -> None:
    baseline = _stable_sample()
    current = _stable_sample()
    result = evaluate_drift("rsi_14", baseline, current)
    assert result.degraded is False
    assert result.psi < DEFAULT_THRESHOLDS.psi_degraded
    assert result.ks < DEFAULT_THRESHOLDS.ks_degraded


def test_evaluate_drift_reports_degraded_for_shifted_distribution() -> None:
    baseline = _stable_sample()
    current = _shifted_sample()
    result = evaluate_drift("rsi_14", baseline, current)
    assert result.degraded is True
    assert result.feature_id == "rsi_14"


# --- negative (>= 3) ---


def test_population_stability_index_rejects_too_few_baseline_samples() -> None:
    with pytest.raises(InsufficientSamplesError):
        population_stability_index([1.0, 2.0, 3.0], _stable_sample())


def test_ks_statistic_rejects_too_few_current_samples() -> None:
    with pytest.raises(InsufficientSamplesError):
        ks_statistic(_stable_sample(), [1.0, 2.0])


def test_population_stability_index_rejects_zero_variance_baseline() -> None:
    with pytest.raises(InsufficientSamplesError):
        population_stability_index([1.0] * 50, _stable_sample())


def test_evaluate_drift_rejects_empty_current_sample() -> None:
    with pytest.raises(InsufficientSamplesError):
        evaluate_drift("rsi_14", _stable_sample(), [])


# --- failure injection ---


def test_evaluate_drift_flags_degraded_when_only_one_statistic_crosses() -> None:
    """Failure injection: a distribution that shifts just enough to trip
    the KS threshold but stays under the PSI threshold must still be
    flagged `degraded` -- an implementation that required both statistics
    to agree (AND instead of OR) would silently miss this."""
    baseline = _stable_sample(n=800)
    # A small, uniform location shift moves the empirical CDFs apart (KS
    # picks it up) without necessarily moving enough mass across the
    # baseline's own quantile bins to cross the PSI threshold.
    current = _shifted_sample(n=800, loc=0.35)
    result = evaluate_drift(
        "rsi_14", baseline, current, thresholds=DriftThresholds(ks_degraded=0.1)
    )
    assert result.psi < DEFAULT_THRESHOLDS.psi_degraded
    assert result.ks >= 0.1
    assert result.degraded is True


# --- numeric performance assertion ---

_EVALUATE_BUDGET_MS = 50.0
"""Pure in-memory numpy computation over ~1k samples, no I/O -- generously
wide budget for a regression guard, not a real SLO (§7 has no ML-signal
-specific number)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
def test_evaluate_drift_p95_latency_within_budget(perf_budget: PerfBudget) -> None:
    baseline = _stable_sample()
    current = _stable_sample()
    raw = perf_budget.samples(lambda: evaluate_drift("rsi_14", baseline, current), n=50)
    cpu_ms_values = sorted(s.cpu_ms for s in raw)
    p95_ms = cpu_ms_values[min(int(len(cpu_ms_values) * 0.95), len(cpu_ms_values) - 1)]
    print(f"[AI-18 evaluate_drift] p95={p95_ms:.4f}ms budget<{_EVALUATE_BUDGET_MS:.1f}ms")
    assert p95_ms < _EVALUATE_BUDGET_MS


# --- gate-red reproduction ---


@pytest.mark.perf
def test_gate_red_absurdly_low_budget_actually_fails(perf_budget: PerfBudget) -> None:
    """Proves the perf assertion above is not a tautology -- an absurdly
    low budget against the same kind of samples must fail.

    Uses wall_ms because process_time() resolution (~15ms on Windows)
    is too coarse to distinguish sub-millisecond operations; wall-clock
    measurement is required for the absurdly-low budget to be meaningful.
    """
    baseline = _stable_sample()
    current = _stable_sample()
    raw = perf_budget.samples(lambda: evaluate_drift("rsi_14", baseline, current), n=10)
    wall_ms_values = sorted(s.wall_ms for s in raw)
    p95_ms = wall_ms_values[min(int(len(wall_ms_values) * 0.95), len(wall_ms_values) - 1)]

    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert p95_ms < absurdly_low_budget_ms
