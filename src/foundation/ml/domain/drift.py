"""AI-18 -- distribution drift rules (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-18
(`domain/drift.py`: "distribution drift determination (PSI/KS)"), §6
failure modes ("model drift -> the signal result carries a `degraded`
flag, new PAPER promotion is blocked" -- the blocking itself is AI-20/21's
job, not yet implemented; this module only produces the verdict).

Only numpy (already a `pyproject.toml` dependency) is used -- no `scipy`
import. CLAUDE.md frequent mistake #8: swapping/adding a third-party
dependency needs an explicit architecture decision on record, not one made
implicitly inside a single leaf; PSI and the two-sample KS statistic are
both simple enough to implement directly against numpy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "DriftThresholds",
    "DriftResult",
    "DEFAULT_THRESHOLDS",
    "InsufficientSamplesError",
    "population_stability_index",
    "ks_statistic",
    "evaluate_drift",
]

_MIN_SAMPLES = 10
"""Below this, a PSI/KS estimate is dominated by sampling noise rather than
the underlying distribution -- fail-closed by raising instead of returning
a misleadingly precise number off a handful of points."""

_PSI_BINS = 10
_PSI_ZERO_BIN_FLOOR = 1e-4
"""Standard PSI convention: a zero-count bin makes `ln(0)` undefined, so
empty bins are floored to this share instead of excluded."""


class InsufficientSamplesError(ValueError):
    """Raised when a baseline or current sample set is too small (< 10) or
    has zero variance -- PSI/KS are undefined or meaningless in that case,
    so this fails closed rather than returning `0.0` (which would read as
    "no drift")."""


@dataclass(frozen=True)
class DriftThresholds:
    """§6 gives no numeric thresholds; these mirror the common industry
    convention (PSI < 0.1 stable, 0.1-0.25 moderate shift, >= 0.25
    significant; KS >= 0.2 significant for large samples) until a later
    leaf ties a tenant-configurable threshold to this contract."""

    psi_degraded: float = 0.25
    ks_degraded: float = 0.2


DEFAULT_THRESHOLDS = DriftThresholds()


@dataclass(frozen=True)
class DriftResult:
    feature_id: str
    psi: float
    ks: float
    degraded: bool


def _as_float_array(values: Sequence[float], *, label: str) -> np.ndarray[Any, Any]:
    array = np.asarray(values, dtype=np.float64)
    if array.size < _MIN_SAMPLES:
        raise InsufficientSamplesError(
            f"{label} needs >= {_MIN_SAMPLES} samples (got {array.size})"
        )
    return array


def population_stability_index(
    baseline: Sequence[float], current: Sequence[float], *, bins: int = _PSI_BINS
) -> float:
    """PSI between `baseline` (a `ModelCard.drift_baseline[feature_id]`
    sample) and `current` (a live sample). Bin edges are baseline quantiles
    so each baseline bin starts with an equal share -- the outer edges are
    extended to +-inf so a `current` value outside the baseline's observed
    range still lands in the nearest bin instead of being silently
    dropped."""
    baseline_arr = _as_float_array(baseline, label="baseline")
    current_arr = _as_float_array(current, label="current")

    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(baseline_arr, quantiles))
    if edges.size < 2:
        raise InsufficientSamplesError("baseline has zero variance; cannot bin for PSI")
    edges = edges.astype(np.float64)
    edges[0] = -np.inf
    edges[-1] = np.inf

    baseline_counts, _ = np.histogram(baseline_arr, bins=edges)
    current_counts, _ = np.histogram(current_arr, bins=edges)
    baseline_pct = baseline_counts / baseline_arr.size
    current_pct = current_counts / current_arr.size
    baseline_pct = np.where(baseline_pct == 0, _PSI_ZERO_BIN_FLOOR, baseline_pct)
    current_pct = np.where(current_pct == 0, _PSI_ZERO_BIN_FLOOR, current_pct)

    return float(np.sum((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)))


def ks_statistic(baseline: Sequence[float], current: Sequence[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic: the largest absolute gap
    between the two samples' empirical CDFs, evaluated at every observed
    value (the standard construction -- the max gap always occurs at one of
    the sample points, so no finer grid is needed)."""
    baseline_arr = np.sort(_as_float_array(baseline, label="baseline"))
    current_arr = np.sort(_as_float_array(current, label="current"))

    combined = np.concatenate([baseline_arr, current_arr])
    cdf_baseline = np.searchsorted(baseline_arr, combined, side="right") / baseline_arr.size
    cdf_current = np.searchsorted(current_arr, combined, side="right") / current_arr.size
    return float(np.max(np.abs(cdf_baseline - cdf_current)))


def evaluate_drift(
    feature_id: str,
    baseline: Sequence[float],
    current: Sequence[float],
    *,
    thresholds: DriftThresholds = DEFAULT_THRESHOLDS,
) -> DriftResult:
    """Compute both statistics and the combined verdict for one feature.
    `degraded` is `True` if *either* statistic crosses its threshold --
    PSI and KS are sensitive to different kinds of shift (PSI to bin-share
    changes, KS to any CDF divergence), so requiring both to agree would
    silently miss a drift only one of them detects."""
    psi = population_stability_index(baseline, current)
    ks = ks_statistic(baseline, current)
    degraded = psi >= thresholds.psi_degraded or ks >= thresholds.ks_degraded
    return DriftResult(feature_id=feature_id, psi=psi, ks=ks, degraded=degraded)
