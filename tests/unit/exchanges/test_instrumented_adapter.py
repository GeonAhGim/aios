"""InstrumentedAdapter 프록시 단위테스트(PM 배정 ⑤ 2단계).

실제 ExchangeAdapter 구현체 대신 가짜 어댑터로, 코루틴 메서드 호출의
성공/실패가 ApiCallTracker에 기록되는지, 프로퍼티/속성은 그대로
통과되는지를 확인한다.
"""
from __future__ import annotations

import asyncio

import pytest

from src.core.safety.metrics_collector import ApiCallTracker
from src.exchanges.common.instrumented_adapter import (
    InstrumentedAdapter,
    instrumented_adapter_factory,
)


class _FakeAdapter:
    def __init__(self) -> None:
        self.is_paper_trading = True
        self.is_sandboxed = True
        self.calls = 0

    async def get_ticker(self, symbol: str) -> str:
        self.calls += 1
        return f"ticker:{symbol}"

    async def place_order(self, order: dict) -> dict:
        self.calls += 1
        raise RuntimeError("exchange rejected order")

    def get_capabilities(self) -> str:
        return "caps"


@pytest.fixture
def tracker() -> ApiCallTracker:
    return ApiCallTracker()


async def test_successful_coroutine_call_is_recorded_and_passed_through(tracker):
    wrapped = InstrumentedAdapter(_FakeAdapter(), tracker)  # type: ignore[arg-type]

    result = await wrapped.get_ticker("BTC/USDT")

    assert result == "ticker:BTC/USDT"
    assert tracker.error_rate_pct() == 0


async def test_failing_coroutine_call_is_recorded_and_reraised(tracker):
    wrapped = InstrumentedAdapter(_FakeAdapter(), tracker)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="exchange rejected order"):
        await wrapped.place_order({})

    assert tracker.error_rate_pct() == 100


def test_properties_pass_through_unmodified(tracker):
    fake = _FakeAdapter()
    wrapped = InstrumentedAdapter(fake, tracker)  # type: ignore[arg-type]

    assert wrapped.is_paper_trading is True
    assert wrapped.is_sandboxed is True


def test_sync_method_passes_through_without_recording(tracker):
    wrapped = InstrumentedAdapter(_FakeAdapter(), tracker)  # type: ignore[arg-type]

    assert wrapped.get_capabilities() == "caps"
    assert tracker.error_rate_pct() == 0


async def test_factory_wraps_base_factory_result(tracker):
    fake = _FakeAdapter()

    def base_factory(exchange, api_key, api_secret, extra, *, demo_mode):
        assert exchange == "bitget"
        assert demo_mode is True
        return fake

    factory = instrumented_adapter_factory(tracker, base_factory)
    wrapped = factory("bitget", "key", "secret", None, demo_mode=True)

    assert isinstance(wrapped, InstrumentedAdapter)
    assert await wrapped.get_ticker("ETH/USDT") == "ticker:ETH/USDT"
    assert tracker.error_rate_pct() == 0


class _FakeAdapterWithSyncTime(_FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.sync_calls = 0

    async def sync_server_time(self) -> None:
        self.sync_calls += 1


async def test_factory_schedules_sync_server_time_when_adapter_supports_it(tracker):
    fake = _FakeAdapterWithSyncTime()
    factory = instrumented_adapter_factory(tracker, lambda *a, **kw: fake)

    factory("bitget", "key", "secret", None, demo_mode=True)
    await asyncio.sleep(0)  # let the scheduled task run

    assert fake.sync_calls == 1


async def test_factory_does_not_schedule_sync_server_time_when_adapter_lacks_it(tracker):
    fake = _FakeAdapter()
    factory = instrumented_adapter_factory(tracker, lambda *a, **kw: fake)

    tasks_before = len(asyncio.all_tasks())
    factory("bitget", "key", "secret", None, demo_mode=True)
    await asyncio.sleep(0)

    assert len(asyncio.all_tasks()) == tasks_before
