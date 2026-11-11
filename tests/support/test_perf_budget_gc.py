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


class _FakeClocks:
    """process_time은 15.625ms 눈금, perf_counter는 연속인 가짜 시계 한 쌍."""

    TICK = 0.015625

    def __init__(self, start: float) -> None:
        self.now = start

    def process_time(self) -> float:
        return (self.now // self.TICK) * self.TICK

    def perf_counter(self) -> float:
        return self.now


def _install(monkeypatch: pytest.MonkeyPatch, clocks: _FakeClocks, *, real: bool = True) -> None:
    """가짜 시계를 건다. real=True면 PerfBudget이 이것을 '진짜 process_time'으로 보게 한다."""
    import time

    import tests.support.perf_budget as pb

    fake = clocks.process_time
    monkeypatch.setattr(time, "process_time", fake)
    monkeypatch.setattr(time, "perf_counter", clocks.perf_counter)
    if real:
        monkeypatch.setattr(pb, "_REAL_PROCESS_TIME", fake)


def test_sub_tick_call_straddling_a_tick_boundary_does_not_read_a_full_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회귀(full CI c65ab2ea9 test_dedupe): 0.3ms 호출이 눈금 경계를 걸치면 CPU가 15.625ms로
    읽혔다."""
    clocks = _FakeClocks(start=0.0156)  # 눈금 직전에서 시작 -> 0.3ms 뒤 눈금을 넘는다
    _install(monkeypatch, clocks)

    def _op() -> None:
        clocks.now += 0.0003

    sample = PerfBudget().sample(_op, batch=1, collect=False)
    assert sample.cpu_ms == pytest.approx(0.3, rel=0.05)  # 벽시계로 잘림 (눈금 15.625가 아니다)


def test_long_call_keeps_the_real_cpu_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """벽시계가 눈금 절반 이상이면 잘라내지 않는다 -- 실제 CPU 측정은 그대로."""
    clocks = _FakeClocks(start=0.0)
    _install(monkeypatch, clocks)

    def _op() -> None:
        clocks.now += 0.040  # 40ms -> process_time 31.25ms(눈금 내림), 잘라낼 이유 없음

    sample = PerfBudget().sample(_op, batch=1, collect=False)
    assert sample.cpu_ms == pytest.approx(31.25)
    assert sample.wall_ms == pytest.approx(40.0)


def test_a_faked_process_time_is_never_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """적색 재현 테스트가 process_time을 가짜로 바꿔 큰 CPU를 가장해도 깎지 않는다(검출력 보존)."""
    clocks = _FakeClocks(start=0.0156)
    _install(monkeypatch, clocks, real=False)  # PerfBudget 입장에서는 '진짜가 아닌' 시계

    def _op() -> None:
        clocks.now += 0.0003

    sample = PerfBudget().sample(_op, batch=1, collect=False)
    assert sample.cpu_ms == pytest.approx(15.625)  # 잘리지 않고 그대로
