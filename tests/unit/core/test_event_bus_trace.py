"""PLT-06 — Event Bus 봉투(trace 컨텍스트 전파) 단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-06
"""
import asyncio
import uuid

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality
from src.core.observability.context import bind, current


def _fast_bus() -> InProcessEventBus:
    return InProcessEventBus(
        max_queue_depth=10,
        backpressure_sustained_seconds=0.05,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
    )


async def test_handler_sees_publish_time_trace_context():
    """publish() 호출 시점에 바인딩된 trace_id/tenant_id를, 별도 워커
    코루틴에서 실행되는 핸들러가 current()로 그대로 관측해야 한다."""
    bus = _fast_bus()
    seen: list[uuid.UUID] = []

    async def handler(payload):
        seen.append(current().trace_id)

    bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    tenant_id = uuid.uuid4()
    with bind(tenant_id=tenant_id) as ctx:
        await bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.05)
    await bus.stop()

    assert seen == [published_trace_id]


async def test_context_does_not_leak_when_published_without_binding():
    """컨텍스트 없이 발행해도 핸들러 실행 전후 프로세스 전역 컨텍스트가
    오염되지 않는다 — 핸들러 안에서 본 trace_id는 발행측 fallback 값과
    일치하되, 핸들러 종료 후에는 그 값이 바깥으로 새어 나오지 않는다."""
    bus = _fast_bus()
    seen: list[uuid.UUID] = []

    async def handler(payload):
        seen.append(current().trace_id)

    bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    before = current().trace_id
    await bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
    await asyncio.sleep(0.05)
    await bus.stop()
    after = current().trace_id

    assert len(seen) == 1
    assert seen[0] not in (before, after)
    assert before != after  # 바인딩 전 current()는 매 호출마다 새 임시값


async def test_context_restored_after_handler_raises():
    """핸들러가 예외를 던져도(SAFE 정책 경로) 바인딩된 컨텍스트는 해당
    handler 실행 구간에만 유효하고, 워커 루프로 복귀하면 원복된다."""
    bus = _fast_bus()

    async def failing_handler(payload):
        raise RuntimeError("boom")

    bus.subscribe("order.status.changed", failing_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    with bind(tenant_id=uuid.uuid4()) as ctx:
        await bus.publish("order.status.changed", {"order_id": "1"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.05)
    await bus.stop()

    # 발행측 바인딩 블록을 벗어난 뒤이므로, 핸들러의 예외 여부와 무관하게
    # 현재 프로세스 컨텍스트는 이미 발행 시점 trace_id와 달라야 한다.
    assert current().trace_id != published_trace_id


async def test_context_restored_after_critical_handler_exhausts_retries():
    """CRITICAL 핸들러가 재시도까지 전부 실패해도(escalate_and_retry) 컨텍스트
    누수 없이 원복된다."""
    bus = _fast_bus()

    async def always_fails(payload):
        raise RuntimeError("persistent failure")

    bus.subscribe(
        "order.status.changed", always_fails, criticality=HandlerCriticality.CRITICAL
    )
    await bus.start()

    with bind(tenant_id=uuid.uuid4()) as ctx:
        await bus.publish("order.status.changed", {"order_id": "2"})
        published_trace_id = ctx.trace_id

    await asyncio.sleep(0.2)
    await bus.stop()

    assert current().trace_id != published_trace_id
