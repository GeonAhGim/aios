"""Snapshot builder fold — PERF-detection (task-17340).

Budget for `test_fold_is_deterministic_across_repeated_runs`: the 200-entry
double fold it performs. Kept in a sibling file so test_snapshot_builder.py stays
well under the 500-line ratchet. The gate-red test injects a 10x virtual delay
(no real sleep/spin) and reuses the exact same measurement and assertion.
"""

from __future__ import annotations

import pytest

from tests.foundation.unit.positions.test_snapshot_builder import _fold, _random_entries
from tests.support.perf_inject import virtual_delayed_sync

_BUDGET_MS = 50.0


def _determinism_check() -> bool:
    entries = _random_entries(seed=7, n=200)
    return _fold(entries) == _fold(entries)


@pytest.mark.perf
def test_fold_determinism_check_within_budget(perf_budget) -> None:
    # batch=10 and best-of-5 keep the Windows process_time tick (~15.6 ms) from
    # collapsing the reading to 0 or spiking it on a single tick.
    sample = perf_budget.best_of(_determinism_check, n=5, batch=10)
    assert sample.result is True
    assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
def test_fold_determinism_budget_fails_under_10x_delay(perf_budget) -> None:
    """Gate-red: a 500 ms virtual delay (10x the budget) per call turns the same
    budget assertion red."""
    slow = virtual_delayed_sync(_determinism_check, 0.5)
    sample = perf_budget.best_of(slow, n=5, batch=2)
    assert sample.cpu_ms >= 10 * _BUDGET_MS
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


def test_slow_wrapper_does_not_change_result_or_hide_mismatch() -> None:
    # Negative: the delay wrapper must stay transparent, and the check itself
    # must still detect a non-deterministic fold.
    assert virtual_delayed_sync(_determinism_check, 0.5)() is True
    entries = _random_entries(seed=7, n=200)
    assert _fold(entries) != _fold(entries[:-1])
