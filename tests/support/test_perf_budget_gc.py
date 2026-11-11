"""task-23258: PerfBudget.sample keeps the cyclic GC out of the timed window."""

from __future__ import annotations

import gc

import pytest

from tests.support.perf_budget import PerfBudget


def test_sample_runs_fn_with_gc_disabled_and_restores_it() -> None:
    seen: list[bool] = []
    assert gc.isenabled()
    PerfBudget().sample(lambda: seen.append(gc.isenabled()), batch=3)
    assert seen == [False, False, False]
    assert gc.isenabled()


def test_sample_restores_gc_when_fn_raises() -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        PerfBudget().sample(boom)
    assert gc.isenabled()


def test_sample_leaves_gc_disabled_if_caller_disabled_it() -> None:
    gc.disable()
    try:
        PerfBudget().sample(lambda: None)
        assert not gc.isenabled()
    finally:
        gc.enable()
