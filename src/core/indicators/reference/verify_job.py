"""IND-13 -- reference vector bulk verification job: deterministic CI sample
+ full-catalog nightly entry point.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
IND-13. Preceding: IND-7g `reference/verify_all.py` (three-way
cross-verification, b613a05), IND-12 `catalog/registry_tiers.py` (934b8d1).

This module never re-implements the three-way comparison -- every check
below calls `verify_all.run_verification` and only adds a deterministic
sampling policy plus a "known permanently unverified" allowlist on top
(decision).

`KNOWN_UNVERIFIED` documents indicators IND-7g classifies as never
verified at some parameter boundary -- a cited, permanent exclusion, not a
regression -- so the CI sample does not flap on them. See
`verify_all.py::test_full_verification_matches_known_state` for the
underlying TA-Lib-vs-our-implementation cause.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from src.core.indicators.reference import verify_all
from src.core.indicators.reference.verify_all import Dataset, VerificationReport

__all__ = [
    "CI_SAMPLE_SIZE",
    "KNOWN_UNVERIFIED",
    "SAMPLE_SEED",
    "SampleCheckResult",
    "run_ci_sample",
    "run_nightly_full",
    "sample_job_names",
]

CI_SAMPLE_SIZE = 30
SAMPLE_SEED = 20260908  # fixed -- changing this reshuffles which names land in the CI sample

KNOWN_UNVERIFIED: dict[str, str] = {
    "BBANDS": (
        "TA-Lib BBANDS variance formula (E[X^2]-E[X]^2) diverges from our "
        "E[(X-mean)^2] implementation (shared by engine.incremental and "
        "engine.vectorized) at the minimum timeperiod=2 boundary -- a "
        "cancellation-error artifact, not a regression. Fixed at "
        "verify_all.py::test_full_verification_matches_known_state; "
        "excluded here so the CI sample does not flap on a known TA-Lib "
        "quirk."
    ),
}


@dataclass(frozen=True)
class SampleCheckResult:
    """Outcome of one CI-sample verification pass."""

    report: VerificationReport
    sample: tuple[str, ...]
    unexpected_excluded: tuple[str, ...]


def sample_job_names(k: int = CI_SAMPLE_SIZE, *, seed: int = SAMPLE_SEED) -> tuple[str, ...]:
    """Fixed-seed sample of `verify_all.VERIFIABLE_NAMES`, minus
    `KNOWN_UNVERIFIED`. Deterministic: same catalog + same seed always
    yields the same sorted tuple (two calls in the same process/commit are
    byte-identical -- see test_reference_sampling.py). Returns the full
    eligible pool once the catalog is smaller than `k` (currently 10)."""
    eligible = tuple(sorted(n for n in verify_all.VERIFIABLE_NAMES if n not in KNOWN_UNVERIFIED))
    if k >= len(eligible):
        return eligible
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(eligible), size=k, replace=False)
    return tuple(sorted(eligible[i] for i in idx))


def run_ci_sample(
    *,
    k: int = CI_SAMPLE_SIZE,
    seed: int = SAMPLE_SEED,
    datasets: Sequence[Dataset] | None = None,
) -> SampleCheckResult:
    """Runs `verify_all.run_verification` over the deterministic sample --
    the check wired into default pytest (tests/unit/core/indicators/, no
    separate CI wiring needed)."""
    sample = sample_job_names(k, seed=seed)
    report = verify_all.run_verification(sample, datasets=datasets)
    unexpected = tuple(n for n in report.excluded if n not in KNOWN_UNVERIFIED)
    return SampleCheckResult(report=report, sample=sample, unexpected_excluded=unexpected)


def run_nightly_full(*, datasets: Sequence[Dataset] | None = None) -> VerificationReport:
    """Full-catalog verification -- entry point for
    `scripts/verify_indicators_nightly.py` (and `@pytest.mark.nightly`
    tests), excluded from the default pytest run by `pyproject.toml`'s
    `addopts = ["-m", "not nightly and not live_demo"]`."""
    return verify_all.run_verification(verify_all.VERIFIABLE_NAMES, datasets=datasets)
