"""4.7 — InProcessEventBus 통합 테스트.

Spec: 08_test_plan_v1.2.md#§8.3 (Event Bus pub/sub), §8.6 (백프레셔)
"""
import asyncio
import time

import pytest

from src.core.event_bus.in_process import (
    BACKPRESSURE_SUSTAINED_TOPIC,
    HANDLER_ESCALATED_TOPIC,
    InProcessEventBus,
)
from src.core.event_bus.policy import HandlerCriticality
from src.core.event_bus.singleton import get_event_bus, reset_event_bus


@pytest.fixture
def fast_bus():
    """빠른 재시도/백프레셔 임계값으로 구성 — 실제 지수 백오프를 초 단위로
    기다리지 않기 위함."""
    return InProcessEventBus(
        max_queue_depth=1,
        backpressure_sustained_seconds=0.05,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
    )


async def test_publish_subscribe_delivers_payload(fast_bus):
    received = []

    async def handler(payload):
        received.append(payload)

    fast_bus.subscribe("market.ticker.updated", handler, criticality=HandlerCriticality.SAFE)
    await fast_bus.start()
    await fast_bus.publish("market.ticker.updated", {"symbol": "BTC/USDT"})
    await asyncio.sleep(0.05)
    await fast_bus.stop()

    assert received == [{"symbol": "BTC/USDT"}]


async def test_safe_handler_error_does_not_affect_other_handlers(fast_bus):
    received = []

    async def failing_handler(payload):
        raise RuntimeError("boom")

    async def healthy_handler(payload):
        received.append(payload)

    fast_bus.subscribe("order.status.changed", failing_handler, criticality=HandlerCriticality.SAFE)
    fast_bus.subscribe("order.status.changed", healthy_handler, criticality=HandlerCriticality.SAFE)
    await fast_bus.start()
    await fast_bus.publish("order.status.changed", {"order_id": "1"})
    await asyncio.sleep(0.05)
    await fast_bus.stop()

    assert received == [{"order_id": "1"}]


async def test_critical_handler_escalates_after_retries_exhausted(fast_bus):
    escalated = []

    async def always_fails(payload):
        raise RuntimeError("persistent failure")

    async def escalation_listener(payload):
        escalated.append(payload)

    fast_bus.subscribe(
        "order.status.changed", always_fails, criticality=HandlerCriticality.CRITICAL
    )
    fast_bus.subscribe(
        HANDLER_ESCALATED_TOPIC, escalation_listener, criticality=HandlerCriticality.SAFE
    )
    await fast_bus.start()
    await fast_bus.publish("order.status.changed", {"order_id": "2"})
    await asyncio.sleep(0.2)
    await fast_bus.stop()

    assert len(escalated) == 1
    assert escalated[0]["topic"] == "order.status.changed"


async def test_backpressure_rejects_publish_when_queue_full(fast_bus):
    # 워커를 시작하지 않아 큐가 절대 비워지지 않게 한 뒤 maxsize(1)를 채운다.
    await fast_bus.publish("market.ticker.updated", {"n": 1})
    await fast_bus.publish("market.ticker.updated", {"n": 2})  # 큐 포화 → 거부

    queue = fast_bus._get_or_create_queue("market.ticker.updated")
    assert queue.qsize() == 1


async def test_sustained_backpressure_escalates_to_meta_topic(fast_bus):
    # 워커가 실제로 동작 중이면 큐가 계속 드레인되어 "지속적으로 가득 참" 상태를
    # 실시간으로 재현하기 어렵다 — time.monotonic()을 전역 패치하면 asyncio
    # 내부 스케줄링까지 깨지므로, 대신 내부 상태(_queue_full_since)에 과거
    # 시각을 직접 심어 임계값 초과 조건만 결정적으로 검증한다.
    sustained = []

    async def listener(payload):
        sustained.append(payload)

    fast_bus.subscribe(BACKPRESSURE_SUSTAINED_TOPIC, listener, criticality=HandlerCriticality.SAFE)
    await fast_bus.start()

    stale = time.monotonic() - (fast_bus._backpressure_sustained_seconds + 1)
    fast_bus._queue_full_since["market.ticker.updated"] = stale
    await fast_bus._handle_backpressure("market.ticker.updated")
    await asyncio.sleep(0.05)
    await fast_bus.stop()

    assert len(sustained) == 1
    assert sustained[0]["topic"] == "market.ticker.updated"


async def test_publish_subscribe_on_audit_decision_logged_topic(fast_bus):
    """06_mvp_scope §6.3 Definition of Done — InProcessEventBus가 최소 3개
    토픽(market.ticker.updated/order.status.changed/audit.decision.logged)
    으로 실제 publish/subscribe 동작해야 한다. 앞의 두 토픽은 이미 위
    테스트들이 증명하고, 세 번째가 빠져 있었다 — 이 토픽도 다른 둘과
    동일하게 순수 EventBus 메커니즘(라우팅/워커/재시도) 대상이지 아직
    실제 프로듀서가 있는 건 아니다(audit_log에 쓰는 20여 곳을 전부
    이 토픽 발행과 묶는 건 이 DoD 항목이 요구하는 범위를 넘는 별도
    설계 작업 — record_audit_log()에 publish 콜백을 추가하고 모든
    호출부를 갱신해야 함)."""
    received = []

    async def handler(payload):
        received.append(payload)

    fast_bus.subscribe("audit.decision.logged", handler, criticality=HandlerCriticality.SAFE)
    await fast_bus.start()
    await fast_bus.publish(
        "audit.decision.logged", {"action_type": "payment.confirmed", "target_id": "123"}
    )
    await asyncio.sleep(0.05)
    await fast_bus.stop()

    assert received == [{"action_type": "payment.confirmed", "target_id": "123"}]


async def test_get_event_bus_returns_same_instance():
    reset_event_bus()
    try:
        assert get_event_bus() is get_event_bus()
    finally:
        reset_event_bus()
