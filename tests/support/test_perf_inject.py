from __future__ import annotations

import asyncio
import time

from tests.support.perf_inject import delayed_async, delayed_sync


def test_delayed_sync_keeps_result_and_adds_delay() -> None:
    wrapped = delayed_sync(lambda a, b=1: a + b, 0.02)
    start = time.perf_counter()
    assert wrapped(2, b=3) == 5
    assert time.perf_counter() - start >= 0.02


def test_delayed_async_keeps_result_and_adds_delay() -> None:
    async def original(x: int) -> list[int]:
        return [x]

    wrapped = delayed_async(original, 0.02)

    async def call() -> list[int]:
        return await wrapped(7)

    start = time.perf_counter()
    assert asyncio.run(call()) == [7]
    assert time.perf_counter() - start >= 0.02
