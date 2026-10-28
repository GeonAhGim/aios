"""가상 지연 주입(perf_inject.virtual_delayed_*) -- sleep/busy-wait 없이 측정치만 느려 보이게 한다.

게이트 적색 재현 테스트가 10배 지연을 실제로 기다리느라 테스트당 최대 126초를 쓰던 것을 대체한다.
측정 로직(PerfBudget)이 같은 숫자를 보므로 예산 단언의 검출력은 실제 지연 주입과 같다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.support.perf_inject import virtual_delayed_async, virtual_delayed_sync, virtual_totals


def _work() -> int:
    return sum(range(100))


async def _awork() -> int:
    return sum(range(100))


@pytest.mark.perf
def test_virtual_sync_delay_shows_in_cpu_and_wall_without_waiting(perf_budget: Any) -> None:
    slow = virtual_delayed_sync(_work, 30.0)
    started = time.perf_counter()
    sample = perf_budget.sample(slow)
    real_elapsed = time.perf_counter() - started
    assert real_elapsed < 5.0  # 30초를 실제로 기다리지 않았다
    assert sample.cpu_ms >= 30_000.0
    assert sample.wall_ms >= 30_000.0
    assert sample.result == 4950


def test_virtual_delay_makes_a_budget_assertion_fail_like_a_real_delay(perf_budget: Any) -> None:
    budget_ms = 50.0
    assert perf_budget.sample(_work).cpu_ms < budget_ms  # 정상 입력은 녹색
    slow = virtual_delayed_sync(_work, 0.5)  # 예산의 10배
    with pytest.raises(AssertionError):
        assert perf_budget.sample(slow).cpu_ms < budget_ms  # 지연 주입은 적색


def test_batch_divides_virtual_delay_per_call(perf_budget: Any) -> None:
    slow = virtual_delayed_sync(_work, 0.4)
    sample = perf_budget.sample(slow, batch=4)
    assert 390.0 <= sample.cpu_ms < 1_000.0  # 4회 호출 평균 = 호출당 0.4s


def test_no_injection_leaves_ledger_and_measurement_untouched(perf_budget: Any) -> None:
    before = virtual_totals()
    sample = perf_budget.sample(_work)
    assert virtual_totals() == before
    assert sample.cpu_ms < 1_000.0


@pytest.mark.perf
def test_virtual_async_delay_adds_wall_only(perf_budget: Any) -> None:
    slow = virtual_delayed_async(_awork, 20.0)
    started = time.perf_counter()
    sample = asyncio.run(perf_budget.sample_async(slow))
    assert time.perf_counter() - started < 5.0
    assert sample.wall_ms >= 20_000.0
    assert sample.cpu_ms < 20_000.0  # 실제 asyncio.sleep처럼 CPU는 늘지 않는다
