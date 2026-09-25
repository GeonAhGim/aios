"""IND-13 -- `reference/verify_job.py` deterministic CI sampling tests.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
IND-13. DoD covered here: (b) sampling is deterministic (two calls in the
same commit yield the same set -- and the test would fail if they didn't),
(c) an intentionally-broken reference vector is caught (nonzero exit,
mismatched name printed), (e) BBANDS is excluded from the sample with its
exclusion reason recorded as a code constant.

This file runs unmarked (no `@pytest.mark.nightly`), so it collects into
the default `tests/unit/core/indicators/` pytest path and runs on every CI
without separate wiring -- the 30-name sample is the everyday gate; the
full catalog is `scripts/verify_indicators_nightly.py`'s job.

DEEPEN (task-2933, docs/audit/DEPTH_DSL_IND.md IND-13 row): the original
leaf (0b50cad8) had explicit-rejection negative coverage of exactly 1 case
(exclusion-reason non-empty). This file adds two more explicit-rejection
cases on `KNOWN_UNVERIFIED` (stale/typo'd catalog membership, reason must
cite its source test) plus a `ValueError` boundary case on `sample_job_names`,
and one numeric performance assertion on `run_ci_sample`'s wall-clock budget.
"""

from __future__ import annotations

import logging
import time

import pytest

from scripts import verify_indicators_nightly
from src.core.indicators.engine import vectorized
from src.core.indicators.engine.vectorized import FloatArray
from src.core.indicators.reference import verify_all, verify_job


def test_sample_job_names_is_deterministic_across_two_calls() -> None:
    first = verify_job.sample_job_names()
    second = verify_job.sample_job_names()
    assert first == second  # fails loudly if two calls in the same commit disagree
    assert len(first) <= verify_job.CI_SAMPLE_SIZE
    assert set(first) <= set(verify_all.VERIFIABLE_NAMES)


def test_sample_job_names_excludes_known_unverified_with_documented_reason() -> None:
    sample = verify_job.sample_job_names()
    for name in verify_job.KNOWN_UNVERIFIED:
        assert name not in sample
        assert verify_job.KNOWN_UNVERIFIED[name].strip()  # exclusion reason is a real string


def test_known_unverified_names_are_real_catalog_members() -> None:
    """A stale or typo'd `KNOWN_UNVERIFIED` key silently excludes nothing --
    it just never matches anything in `sample_job_names`'s eligible pool.
    Reject that drift explicitly instead of letting it hide."""
    for name in verify_job.KNOWN_UNVERIFIED:
        assert name in verify_all.VERIFIABLE_NAMES, (
            f"{name} is not in verify_all.VERIFIABLE_NAMES -- exclusion is a no-op"
        )


def test_known_unverified_reason_cites_its_source_test() -> None:
    """The exclusion reason must point at the underlying evidence (IND-7g's
    fixed-known-state test), not just be any non-empty string -- otherwise a
    future edit could hollow it out to a placeholder and still pass the
    weaker non-empty check."""
    for reason in verify_job.KNOWN_UNVERIFIED.values():
        assert "verify_all.py" in reason
        assert "test_full_verification_matches_known_state" in reason


def test_sample_job_names_caps_at_eligible_catalog_size() -> None:
    eligible = set(verify_all.VERIFIABLE_NAMES) - set(verify_job.KNOWN_UNVERIFIED)
    assert set(verify_job.sample_job_names(30)) == eligible
    small = verify_job.sample_job_names(3)
    assert len(small) == 3
    assert small == tuple(sorted(small))


def test_ci_sample_has_no_unexpected_exclusions() -> None:
    """The everyday gate: today's catalog, run through the real three-way
    verifier, must pass cleanly once known TA-Lib quirks are allowlisted."""
    result = verify_job.run_ci_sample()
    assert result.unexpected_excluded == ()
    assert set(result.report.excluded) <= set(verify_job.KNOWN_UNVERIFIED)


@pytest.mark.perf
def test_ci_sample_completes_within_latency_budget() -> None:
    """Numeric performance assertion: the 30-name CI sample runs on every
    commit (unmarked, no `nightly` gate), so a regression that made it scale
    like the full catalog (or worse) must show up as a real gate failure, not
    just a slow-CI complaint. Measured baseline is ~0.1s on CI hardware; 5s
    leaves >40x headroom for slower machines without masking an actual
    blow-up (e.g. accidentally running the full corpus per call)."""
    start = time.perf_counter()
    verify_job.run_ci_sample()
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"CI sample took {elapsed:.3f}s, budget is 5.0s"


# ---------------------------------------------------------------- negative --


def test_sample_job_names_rejects_negative_sample_size() -> None:
    """`k` is a count, not an offset -- a negative sample size must fail
    closed instead of silently returning something (e.g. the full pool)."""
    with pytest.raises(ValueError):
        verify_job.sample_job_names(-1)


def test_injected_reference_drift_is_caught_by_ci_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake provider that quietly drifts one indicator's vectorized output
    must show up as an unexpected exclusion, not pass silently."""
    original = vectorized._KERNELS["SMA"]

    def drifted(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
        (out,) = original(c, p)
        return (out * (1.0 + 1e-3),)

    monkeypatch.setitem(vectorized._KERNELS, "SMA", drifted)
    result = verify_job.run_ci_sample()
    assert "SMA" in result.unexpected_excluded
    assert any(m.name == "SMA" for m in result.report.mismatches)


def test_nightly_script_exits_nonzero_and_prints_mismatch_name_on_drift(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reproduces DoD(c) end-to-end through the actual script entry point:
    intentionally break one reference vector and confirm the nightly job
    fails closed with the offending indicator name in its output."""
    original = vectorized._KERNELS["RSI"]

    def drifted(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
        (out,) = original(c, p)
        return (out + 5.0,)

    monkeypatch.setitem(vectorized._KERNELS, "RSI", drifted)
    with caplog.at_level(logging.WARNING):
        code = verify_indicators_nightly.main()
    assert code != 0
    assert any("MISMATCH RSI" in record.message for record in caplog.records)


@pytest.mark.nightly
def test_nightly_full_verification_has_no_unexpected_exclusions() -> None:
    """Full-catalog counterpart of `test_ci_sample_has_no_unexpected_exclusions`
    -- runs only under `pytest -m nightly`, never in the default suite."""
    report = verify_job.run_nightly_full()
    unexpected = [n for n in report.excluded if n not in verify_job.KNOWN_UNVERIFIED]
    assert unexpected == []
