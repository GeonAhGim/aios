import asyncio

import pytest

from src.core.safety.base_loop import run_safety_loop


async def test_loop_survives_tick_exceptions_and_keeps_calling():
    calls = []

    async def tick():
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("boom")

    task = asyncio.create_task(run_safety_loop("test", 0.01, tick))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 3  # 예외가 나도 계속 호출됨


async def test_loop_stops_cleanly_on_cancellation():
    async def tick():
        pass

    task = asyncio.create_task(run_safety_loop("test", 0.01, tick))
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
