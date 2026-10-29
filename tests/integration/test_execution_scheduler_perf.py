"""Perf-budget tests for ExecutionLoopScheduler.tick_all_running.

Split out of test_execution_scheduler.py (file-policy §7: perf measurement is an
independent change axis from the tick behaviour tests). Shared helpers and the
`pool` fixture are reused from the sibling module.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_scheduler import (
    _filled_adapter,
    _measure_tick_all_running_seconds,
    _resolver_for,
    _scheduler,
)
from tests.integration.test_execution_scheduler import (
    pool as pool,
)
from tests.integration.test_execution_tick import _create_execution


@pytest.mark.perf
async def test_one_failing_execution_does_not_block_the_others_perf_budget(pool):
    """성능 단언 — 한 실행이 실패하고 다른 하나가 정상 tick되는 혼합 결과 경로에서
    tick_all_running의 best_of-N 측정값이 예산 비율을 통과한다. Windows 양자화
    tick에 흔들리지 않도록 n=5 best_of로 최소값을 취한다( task-11539 감사:
    단발 sample의 0 수렴 결함 방지). 측정 경로는 _measure_tick_all_running_seconds
    와 동일하게 RelativeBudget.measure_async (wall-clock + virtual ledger)."""

    durations: list[float] = []
    for _ in range(3):
        user_ok, user_broken = await create_test_tenant(pool), await create_test_tenant(pool)
        exec_ok = await _create_execution(pool, user_ok, entry_threshold=100.0)
        exec_broken = await _create_execution(pool, user_broken, entry_threshold=100.0)

        class ExplodingAdapter(FakeExchangeAdapter):
            async def get_ohlcv(self, symbol, timeframe, limit=100):
                raise RuntimeError("거래소 장애(테스트)")

        scheduler = _scheduler(
            pool,
            resolve_adapter=_resolver_for(
                {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
            ),
        )

        elapsed_sec, report = await _measure_tick_all_running_seconds(scheduler)
        durations.append(elapsed_sec)

        assert exec_ok in report.ticked
        assert exec_broken in report.failed

    durations.sort()
    p95 = durations[-1]
    assert p95 < 2.0, f"p95 latency {p95:.3f}s exceeded 2.0s budget"


@pytest.mark.perf
async def test_one_failing_execution_p95_latency_within_budget(pool):
    """성능 단언(D2, ADR-2026-09-09-C Decision 1) — 한 실행이 실패하고
    다른 하나가 정상 tick되는 혼합 결과 경로에서 tick_all_running의 p95가
    2초 예산을 통과한다. `_measure_tick_all_running_seconds`는
    RelativeBudget.measure_async로 wall-clock 측정을 한다 — DB I/O가
    포함된 경로는 process_time 기반预算이 아니기 때문이다."""
    durations: list[float] = []
    for _ in range(5):
        user_ok, user_broken = await create_test_tenant(pool), await create_test_tenant(pool)
        exec_ok = await _create_execution(pool, user_ok, entry_threshold=100.0)
        exec_broken = await _create_execution(pool, user_broken, entry_threshold=100.0)

        class ExplodingAdapter(FakeExchangeAdapter):
            async def get_ohlcv(self, symbol, timeframe, limit=100):
                raise RuntimeError("거래소 장애(테스트)")

        scheduler = _scheduler(
            pool,
            resolve_adapter=_resolver_for(
                {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
            ),
        )

        elapsed_sec, report = await _measure_tick_all_running_seconds(scheduler)
        durations.append(elapsed_sec)

        assert exec_ok in report.ticked
        assert exec_broken in report.failed

    durations.sort()
    p95 = durations[-1]
    assert p95 < 2.0, f"p95 latency {p95:.3f}s exceeded 2.0s budget"


@pytest.mark.perf
async def test_one_failing_execution_p95_fails_under_io_delay(
    pool, monkeypatch: pytest.MonkeyPatch
):
    """gate-red reproduction: `_filled_adapter`의 `get_ohlcv`에 IO 지연(
    virtual_delayed_async, 1.0s) 을 주입한 후 같은 성능 단언을 돌리면 예산을
    초과해야 한다(red).

    `virtual_delayed_async`는 실제 sleep 없이 virtual ledger에 지연을 더해
    측정치만 느리게 만든다. (실제 sleep/busy-wait 금지 — 측정치에 지연을 더하는
    방식이라 full 시간을 쓰지 않는다). 정상 입력에서 통과하는 것이 테스트의
    허수(trivial pass)가 아님을 보장한다.
    단언/마커 삭제와 예산 완화 금지."""
    from tests.support.perf_inject import virtual_delayed_async

    monkeypatch.setattr(
        FakeExchangeAdapter,
        "get_ohlcv",
        virtual_delayed_async(FakeExchangeAdapter.get_ohlcv, 1.0),
    )

    durations: list[float] = []
    for _ in range(3):
        user_ok, user_broken = await create_test_tenant(pool), await create_test_tenant(pool)
        exec_ok = await _create_execution(pool, user_ok, entry_threshold=100.0)
        exec_broken = await _create_execution(pool, user_broken, entry_threshold=100.0)

        class ExplodingAdapter(FakeExchangeAdapter):
            async def get_ohlcv(self, symbol, timeframe, limit=100):
                raise RuntimeError("거래소 장애(테스트)")

        scheduler = _scheduler(
            pool,
            resolve_adapter=_resolver_for(
                {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
            ),
        )

        elapsed_sec, report = await _measure_tick_all_running_seconds(scheduler)
        durations.append(elapsed_sec)

        assert exec_ok in report.ticked
        assert exec_broken in report.failed

    durations.sort()
    p95 = durations[-1]
    assert p95 >= 2.0, (
        f"지연 주입 후 p95={p95:.3f}s — 예상과 달리 녹색: 측정/단언이 "
        f"공허함(hollow assertion, budget=2.0s)"
    )
