"""M2-8 red-gate 재현 — Redis 없이 항상 돈다(기본 CI 스위트 포함).

`InProcessEventBus`는 프로세스 경계를 넘는 영속성이 전혀 없어, 발행 후
"재시작" 시나리오에서 이벤트가 그냥 사라진다. 이게 M2-8이 고치는 결함의
red baseline이고, `test_redis_streams_event_bus.py::test_crash_then_replay_no_loss`
(`pytest -m redis`로 명시 실행)가 그 수정을 증명하는 짝이다.
"""

from __future__ import annotations

import asyncio
import time

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality


async def _scenario() -> int:
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    crashed = InProcessEventBus()
    crashed.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    # start()를 의도적으로 부르지 않는다 — 워커가 뜨기 전에 프로세스가 죽는
    # 최악의 경우(가장 흔한 크래시 시점)를 재현한다.
    for i in range(5):
        await crashed.publish("topic", {"seq": i})
    del crashed  # "크래시" — in-memory asyncio.Queue와 그 내용물이 사라진다

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    return len(received)


async def test_in_process_bus_loses_events_on_crash_baseline():
    processed = await _scenario()
    assert processed == 0  # red baseline — in_process는 구조적으로 이걸 고칠 수 없다


async def test_in_process_bus_loses_events_even_when_started_before_crash():
    """negative — "start()를 안 불러서 그렇다"는 가설을 기각한다.

    워커가 이미 떠서 큐를 소비 중이어도, 핸들러가 끝까지 처리하기 전에
    프로세스가 죽으면(in-flight 이벤트) 영속성이 없으므로 그대로 사라진다.
    """
    received: list[int] = []

    async def slow_handler(payload):
        await asyncio.sleep(10)  # 절대 제시간에 끝나지 않는다 — "처리 중" 상태를 고정
        received.append(payload["seq"])

    crashed = InProcessEventBus()
    crashed.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.start()
    await crashed.publish("topic", {"seq": 0})
    await asyncio.sleep(0.05)  # 워커가 큐에서 꺼내 핸들러 실행에 들어갈 시간을 준다
    for task in crashed._worker_tasks.values():
        task.cancel()
    del crashed  # "크래시" — in-flight 이벤트와 워커 상태 모두 소실

    restarted = InProcessEventBus()
    restarted.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    assert received == []  # in-flight 이벤트는 재시작해도 복구되지 않는다


async def test_in_process_bus_backpressure_rejection_does_not_persist_event():
    """negative — 백프레셔로 거부된 이벤트가 어딘가에 보관됐다가 나중에
    재생되는 것이 아니라, 그냥 영구히 사라진다는 것을 확인한다."""
    bus = InProcessEventBus(max_queue_depth=1)
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus.subscribe("topic", handler, criticality=HandlerCriticality.SAFE)
    # start()를 부르지 않아 워커가 큐를 비우지 않으므로 두 번째 publish는 가득 찬 큐를 만난다.
    await bus.publish("topic", {"seq": 0})
    await bus.publish("topic", {"seq": 1})  # 큐가 가득 차 거부된다 (§8.6)

    await bus.start()
    await asyncio.sleep(0.1)
    await bus.stop()
    # seq=1은 거부된 시점에 영구히 사라졌으므로 재생되지 않는다.
    assert received == [0]


async def test_in_process_bus_multiple_topics_all_lose_events_on_crash():
    """negative — 손실이 특정 토픽 하나에 국한된 우연이 아니라, 토픽 수와
    무관하게 구조적으로 전부에 적용된다는 것을 확인한다."""
    received: dict[str, list[int]] = {"a": [], "b": [], "c": []}

    async def make_handler(topic: str):
        async def handler(payload):
            received[topic].append(payload["seq"])

        return handler

    crashed = InProcessEventBus()
    for topic in received:
        crashed.subscribe(topic, await make_handler(topic), criticality=HandlerCriticality.CRITICAL)
    for topic in received:
        await crashed.publish(topic, {"seq": 1})
    del crashed

    restarted = InProcessEventBus()
    for topic in received:
        restarted.subscribe(
            topic, await make_handler(topic), criticality=HandlerCriticality.CRITICAL
        )
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert all(received[topic] == [] for topic in received)


async def test_in_process_bus_audit_sink_failure_does_not_rescue_lost_events():
    """failure-injection — audit_sink가 예외를 던지는 상황을 주입해도,
    (a) 워커가 죽지 않고 (b) 크래시로 소실된 이벤트가 어떤 식으로든
    복구되지 않는다는 것을 함께 검증한다."""

    async def failing_audit_sink(record):
        raise RuntimeError("audit backend down")

    received: list[int] = []

    async def failing_handler(payload):
        raise ValueError("handler boom")

    crashed = InProcessEventBus(
        audit_sink=failing_audit_sink, max_retries=1, retry_initial_delay_seconds=0.01
    )
    crashed.subscribe("topic", failing_handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.start()
    await crashed.publish("topic", {"seq": 0})
    await asyncio.sleep(0.2)
    await crashed.stop()  # audit_sink 예외가 워커를 죽이지 않았다면 정상 종료된다
    del crashed

    async def ok_handler(payload):
        received.append(payload["seq"])

    restarted = InProcessEventBus()
    restarted.subscribe("topic", ok_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # 실패한 audit_sink도 소실된 이벤트를 되살리지 못한다


async def test_in_process_bus_crash_detection_perf_budget():
    """perf — 이 red-gate 재현 자체가 CI 기본 스위트에 상시 포함되므로,
    시나리오 1회 실행이 예산(500ms) 안에 끝나야 한다(p99 budget, ADR-2026-09-09-C)."""
    start = time.perf_counter()
    processed = await _scenario()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert processed == 0
    assert elapsed_ms < 500, f"red-gate scenario took {elapsed_ms:.1f}ms, budget=500ms"
