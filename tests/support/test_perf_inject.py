from __future__ import annotations

import asyncio
import time

import pytest

from tests.support.perf_inject import delayed_async, delayed_sync


@pytest.mark.perf
def test_delayed_sync_keeps_result_and_adds_delay() -> None:
    wrapped = delayed_sync(lambda a, b=1: a + b, 0.02)
    start = time.perf_counter()
    assert wrapped(2, b=3) == 5
    assert time.perf_counter() - start >= 0.02


@pytest.mark.perf
def test_delayed_async_keeps_result_and_adds_delay() -> None:
    async def original(x: int) -> list[int]:
        return [x]

    wrapped = delayed_async(original, 0.02)

    async def call() -> list[int]:
        return await wrapped(7)

    start = time.perf_counter()
    assert asyncio.run(call()) == [7]
    assert time.perf_counter() - start >= 0.02


def test_delayed_sync_injects_cpu_time_even_when_the_process_is_descheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU 경쟁 모사: CPU 시계가 벽시계의 1/4 속도여도 주입한 만큼 CPU 시간이 쌓여야 한다.

    옛 구현(벽시계 기준)은 경쟁 시 200ms 주입이 31~172ms만 쌓여 `DID NOT RAISE`가 났다.
    """
    real_perf = time.perf_counter
    start = real_perf()

    def slow_cpu_clock() -> float:  # 벽시계 경과의 25%만 CPU 시간으로 센다
        return (real_perf() - start) * 0.25

    monkeypatch.setattr(time, "process_time", slow_cpu_clock)
    cpu_before = slow_cpu_clock()
    wall_before = real_perf()
    delayed_sync(lambda: None, 0.05)()
    assert slow_cpu_clock() - cpu_before >= 0.05  # CPU 시간이 목표 이상 쌓였다
    assert real_perf() - wall_before >= 0.05 * 4 - 0.01  # 그만큼 벽시계는 더 걸렸다


def test_delayed_sync_gives_up_on_a_fully_starved_cpu_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_time이 멈춰도(굶음) 벽시계 상한에서 끝난다 -- 테스트가 매달리지 않는다."""
    monkeypatch.setattr(time, "process_time", lambda: 0.0)
    started = time.perf_counter()
    delayed_sync(lambda: None, 0.002)()
    assert time.perf_counter() - started < 0.002 * 50 + 0.5


def test_delayed_sync_still_waits_the_full_wall_time_when_cpu_clock_runs_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_time이 15.6ms 단위로 앞서 뛰어도(CPU 시계가 빠름) 벽시계로 주입 시간은 채운다.

    test_canary_bitget: 50ms 주입인데 wall=48.7ms로 `wall >= 50` 단언이 깨졌다.
    """
    real_perf = time.perf_counter
    start = real_perf()
    monkeypatch.setattr(time, "process_time", lambda: (real_perf() - start) * 10.0)
    before = real_perf()
    delayed_sync(lambda: None, 0.05)()
    assert real_perf() - before >= 0.05
