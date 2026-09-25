"""`background_loops.py`의 PLT-08 계측 래퍼(`_run_instrumented`/`run_periodic_loop`)
전용 단위 테스트 — DEEPEN(task-3155, 원 리프 task-938).

이전엔 이 래퍼들에 대한 전용 테스트가 전혀 없었다(`test_loop_health.py`는
`LoopHealth` 자체만, `test_background_loops_wiring.py`는 OMS outbox 배선만
본다). 이 래퍼가 실제로 지키는 계약 두 가지:
1. tick 예외를 삼켜 루프 태스크를 죽이지 않는다(heartbeat 제외 — 그건
   `_heartbeat_loop`가 직접 구현하고 별도로 테스트됨).
2. `run_periodic_loop`은 tick 전에 먼저 sleep한다 — 취소되면 tick이 0회다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-08.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import pytest

from src.core.observability.loop_health import LoopHealth
from src.services.background_loops import _run_instrumented, run_periodic_loop

pytestmark = pytest.mark.asyncio


async def test_run_instrumented_success_records_ok_tick() -> None:
    health = LoopHealth()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1

    await _run_instrumented("probe", 1.0, tick, health=health, on_error="boom")

    assert calls == 1
    status = health.snapshot()["probe"]
    assert status.consecutive_failures == 0
    assert status.last_success_at is not None


async def test_run_instrumented_swallows_exception_and_records_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """negative — tick이 예외를 던져도 `_run_instrumented`는 그 예외를 밖으로
    다시 던지지 않는다(heartbeat 전용 재던지기와 대조되는, 나머지 3루프의
    fail-closed가 아니라 "죽지 않는다" 계약)."""
    health = LoopHealth()

    async def failing_tick() -> None:
        raise RuntimeError("tick 내부 실패")

    with caplog.at_level(logging.ERROR):
        await _run_instrumented(
            "probe", 1.0, failing_tick, health=health, on_error="probe_loop: 실패"
        )

    status = health.snapshot()["probe"]
    assert status.consecutive_failures == 1
    assert status.last_success_at is None
    assert any("probe_loop: 실패" in r.getMessage() for r in caplog.records)


async def test_run_periodic_loop_does_not_tick_before_first_sleep() -> None:
    """negative — tick은 `sleep(interval_sec)` 이후에만 실행된다. 취소가
    sleep 도중 들어오면 tick 호출 자체가 0회여야 한다."""
    health = LoopHealth()
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1

    task = asyncio.create_task(
        run_periodic_loop("probe", 10.0, tick, health=health, on_error="boom")
    )
    await asyncio.sleep(0.02)  # interval(10s)보다 훨씬 짧게만 양보 — 아직 sleep 중
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls == 0
    assert health.snapshot() == {}


async def test_run_periodic_loop_cancellation_mid_sleep_leaves_no_partial_tick() -> None:
    """negative — 취소가 tick 실행 도중이 아니라 sleep 구간에서 들어왔을 때,
    다음 loop iteration으로 흘러가지 않고 태스크가 정확히 멈춘다(태스크
    누수/좀비 tick 없음)."""
    health = LoopHealth()

    async def tick() -> None:
        raise AssertionError("이 테스트에서는 tick이 절대 호출되면 안 된다")

    task = asyncio.create_task(
        run_periodic_loop("probe", 5.0, tick, health=health, on_error="boom")
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_run_periodic_loop_survives_failure_and_ticks_again() -> None:
    """실패 주입 — 1회차 tick이 예외를 내도 태스크가 죽지 않고 2회차 tick이
    실행돼 성공을 기록한다(4개 코어 루프가 "한 번 실패하면 그다음부터
    영원히 멈춘다"로 회귀하지 않는지 증명하는 핵심 계약)."""
    health = LoopHealth()
    attempts: list[int] = []

    async def flaky_tick() -> None:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise RuntimeError("첫 tick만 실패")

    task = asyncio.create_task(
        run_periodic_loop("flaky", 0.01, flaky_tick, health=health, on_error="flaky 실패")
    )
    try:
        for _ in range(200):
            snap = health.snapshot()
            status = snap.get("flaky")
            if status is not None and status.consecutive_failures == 0 and len(attempts) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("실패 후 재시도 tick이 시한 안에 성공하지 못했다 — 루프가 멈췄다")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(attempts) >= 2
    assert health.snapshot()["flaky"].consecutive_failures == 0


# ADR-2026-09-09-C Decision 1 예산표엔 루프 계측 전용 항목이 없다 — I/O 없는
# 동기 판정 로직이라는 성질이 같은 "사전거래 게이트 p99 5ms"를 가장 가까운
# 유사 항목으로 차용한다(task-3153/rate_limit_storm DEEPEN과 동일 관례).
_INSTRUMENTATION_OVERHEAD_BUDGET_S = 0.005


async def _measure_p95_overhead(health: LoopHealth, *, iterations: int = 30) -> float:
    async def noop_tick() -> None:
        return None

    durations: list[float] = []
    for _ in range(iterations):
        start = time.monotonic()
        await _run_instrumented("perf", 1.0, noop_tick, health=health, on_error="boom")
        durations.append(time.monotonic() - start)
    durations.sort()
    return durations[int(len(durations) * 0.95)]


async def test_run_instrumented_overhead_p95_within_budget() -> None:
    """수치 성능 단언 — no-op tick을 감싸는 계측 오버헤드(bind_system +
    record_tick) 자체의 p95가 예산 내인지 본다."""
    health = LoopHealth()

    p95 = await _measure_p95_overhead(health)

    assert p95 < _INSTRUMENTATION_OVERHEAD_BUDGET_S, (
        f"p95={p95:.6f}s >= budget={_INSTRUMENTATION_OVERHEAD_BUDGET_S}s"
    )


async def test_run_instrumented_overhead_assertion_fails_when_health_is_slow() -> None:
    """게이트 적색 재현 — `record_tick`에 예산의 여러 배 지연을 주입하면 위
    p95 단언이 실제로 AssertionError를 내는지 확인한다(위 테스트가
    tautology가 아님을 증명)."""

    class _SlowLoopHealth(LoopHealth):
        def record_tick(
            self, loop: str, ok: bool, duration_s: float, *, interval_sec: float | None = None
        ) -> None:
            time.sleep(_INSTRUMENTATION_OVERHEAD_BUDGET_S * 3)
            super().record_tick(loop, ok, duration_s, interval_sec=interval_sec)

    slow_health = _SlowLoopHealth()

    p95 = await _measure_p95_overhead(slow_health, iterations=5)

    with pytest.raises(AssertionError):
        assert p95 < _INSTRUMENTATION_OVERHEAD_BUDGET_S
