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


def test_samples_collects_once_per_series_not_per_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """회귀(2026-11-11): 샘플마다 gc.collect()하면 큰 힙의 스위트 워커에서 500샘플이 수 분 걸려
    pytest-timeout(os._exit)이 워커를 죽였다. 시리즈당 한 번만 수집한다."""
    calls = 0
    real_collect = gc.collect

    def _counting_collect(*args: int) -> int:
        nonlocal calls
        calls += 1
        return real_collect(*args)

    monkeypatch.setattr(gc, "collect", _counting_collect)
    samples = PerfBudget().samples(lambda: None, n=50, warmup=0)
    assert len(samples) == 50
    assert calls == 1


def test_single_sample_still_collects_before_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    real_collect = gc.collect

    def _counting_collect(*args: int) -> int:
        nonlocal calls
        calls += 1
        return real_collect(*args)

    monkeypatch.setattr(gc, "collect", _counting_collect)
    PerfBudget().sample(lambda: None)
    assert calls == 1
