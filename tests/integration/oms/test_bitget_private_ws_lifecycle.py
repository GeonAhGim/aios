"""L4-20 Bitget private WS lifecycle integration coverage."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from src.exchanges.bitget.private_ws_mixin import (
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.bitget_private_ws_support import (
    _FakeConnection,
    _never,
    _no_rest_calls_adapter,
    _TrackingConnectCtx,
)
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


@pytest.mark.perf
@pytest.mark.parametrize("failure_phase", ["login", "subscribe", "receive"])
async def test_cancelling_subscription_closes_the_connection(pool, perf_budget, failure_phase):
    """DoD(d) — 종료 시 구독이 실제로 해제된다(누수 금지). 태스크를
    취소하면 `WsSession.run`의 `async with connect_fn(...)` 블록이
    빠져나가며 연결 컨텍스트매니저의 `__aexit__`가 호출돼야 한다.

    task-23041: best-of-5, batch=20 cancellation lifecycle wall measurements
    were 0.258115/0.137925/0.120235 ms on Windows Python 3.13. The new
    100 ms budget gives >387x headroom over the largest local measurement.
    Virtual delay injects 10x this budget into the same lifecycle assertion.
    INVARIANTS.md cross-check: trading/ledger replay invariants are N/A here:
    no order event is delivered; repeated lifecycle checks cover task leakage.
    """
    connection = _FakeConnection([], hang=True)
    ctx = _TrackingConnectCtx(connection)

    def connect_fn(url: str):
        return ctx

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)
    task = asyncio.create_task(
        subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert not task.done()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ctx.exited is True  # 연결이 실제로 정리됐다(구독 해제, 누수 금지)

    from tests.support.perf_inject import virtual_delayed_async

    class InjectedFailure(RuntimeError):
        pass

    class FailingConnection(_FakeConnection):
        async def send(self, message):
            operation = json.loads(message)["op"]
            if operation == failure_phase:
                raise InjectedFailure(f"injected {failure_phase} failure")
            await super().send(message)

    failed_connection = FailingConnection(
        [], raise_after=InjectedFailure("injected receive failure")
    )
    failed_ctx = _TrackingConnectCtx(failed_connection)
    with pytest.raises(InjectedFailure, match=f"injected {failure_phase} failure"):
        await asyncio.wait_for(
            subscribe_bitget_orders_to_inbox(
                adapter, inbox, connect_fn=lambda url: failed_ctx, ping_sleep_fn=_never
            ),
            timeout=5,
        )
    assert failed_ctx.exited is True

    def check_closed(observed):
        assert observed.exited is True, "connection cleanup regression"

    async def cancellation_roundtrip():
        ready = asyncio.Event()
        stopped = asyncio.Event()
        child_tasks = []

        class ReadyConnection(_FakeConnection):
            async def _iter(self):
                child_tasks.append(asyncio.current_task())
                ready.set()
                try:
                    async for message in super()._iter():
                        yield message
                finally:
                    stopped.set()

        async def ping_wait(delay):
            child_tasks.append(asyncio.current_task())
            await _never(delay)

        round_ctx = _TrackingConnectCtx(ReadyConnection([], hang=True))
        subscription = asyncio.create_task(
            subscribe_bitget_orders_to_inbox(
                adapter, inbox, connect_fn=lambda url: round_ctx, ping_sleep_fn=ping_wait
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            assert not subscription.done()
            subscription.cancel()
            with pytest.raises(asyncio.CancelledError):
                await subscription
            check_closed(round_ctx)
            assert stopped.is_set()
            assert len(child_tasks) == 2
            assert all(child.done() for child in child_tasks)
        finally:
            subscription.cancel()
            await asyncio.gather(subscription, return_exceptions=True)
        return round_ctx

    # Cancellation must drain both heartbeat and receive tasks before returning.
    # Repeating the lifecycle also verifies that a failed session does not poison recovery.
    budget_ms = 100.0

    async def check_budget(operation):
        sample = await perf_budget.best_of_async(operation, n=5, batch=20)
        assert sample.wall_ms < budget_ms, "cancellation budget exceeded"
        return sample

    try:
        sample = await check_budget(cancellation_roundtrip)
        print(f"cancellation baseline cpu={sample.cpu_ms:.6f} wall={sample.wall_ms:.6f} ms")
        with pytest.raises(AssertionError, match="cancellation budget exceeded"):
            await check_budget(virtual_delayed_async(cancellation_roundtrip, budget_ms * 10 / 1000))
        # Corrupt the cleanup observation; the production-path assertion must reject it.
        observed = sample.result
        observed.exited = False
        with pytest.raises(AssertionError, match="connection cleanup regression"):
            check_closed(observed)
    finally:
        await adapter.aclose()
