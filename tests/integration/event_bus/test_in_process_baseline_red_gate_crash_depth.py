"""M2-8 red-gate 심화 — gate-red 및 perf-injection (task-25158 회수, task-25241).

`test_in_process_baseline_red_gate.py`가 500줄 근처라 별도 파일로 분리했다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


async def test_in_process_bus_multiple_topics_gate_red_corrupt_payload():
    """gate-red — 단언이 실제로 잡히는지를 검증한다.

    정상 payload(dict) 대신 망가진 입력(None)을 넣어도, InProcessEventBus가
    이벤트를 보존하지 않는다는 동일한 결론에 도달한다.
    """
    received: list[int] = []

    async def handler(payload):
        received.append(payload.get("seq", -1))

    crashed = InProcessEventBus()
    crashed.subscribe("a", handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.publish("a", {"seq": 1})
    corrupt: Any = None  # 의도적 망가짐
    await crashed.publish("a", corrupt)
    del crashed

    restarted = InProcessEventBus()
    restarted.subscribe("a", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # gate-red: 망가진 입력도 구조적으로 보존되지 않음


async def test_in_process_bus_multiple_topics_gate_red_corrupt_topic_name():
    """gate-red — 토픽 이름을 망가뜨려 구독과 발행이 다른 토픽을 가리키게 한다.

    구독은 "topic_a"지만 발행은 "topic_b" — 이벤트가 다른 토픽 큐로 가고
    핸들러가 구독하지 않으므로 손실된다.
    """
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    crashed = InProcessEventBus()
    crashed.subscribe("topic_a", handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.publish("topic_b", {"seq": 1})
    del crashed

    restarted = InProcessEventBus()
    restarted.subscribe("topic_a", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # gate-red: 토픽 불일치 시 이벤트 손실 확인


async def test_in_process_bus_multiple_topics_perf_injection_gate_red(perf_budget):
    """perf-injection gate-red — 가상 지연 10배 주입으로 적색 재현 + best_of 측정.

    perf_inject.virtual_delayed_async로 publish를 10배 느리게 가장한 뒤,
    perf_budget.best_of로 여러 번 측정한다.
    """
    reset_virtual_totals()

    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    original_publish = InProcessEventBus.publish
    InProcessEventBus.publish = virtual_delayed_async(original_publish, 0.1)

    try:
        sample = await perf_budget.sample_async(lambda: _perf_scenario(received))
        assert sample.wall_ms >= 50.0, (
            f"gate-red perf: virtual delay injection failed — "
            f"expected wall_ms >= 50 but got {sample.wall_ms:.1f}ms"
        )
        assert sample.result == 0
    finally:
        InProcessEventBus.publish = original_publish


async def _perf_scenario(received: list[int]) -> int:
    """Helper for perf-injection gate-red test."""
    bus = InProcessEventBus()
    bus.subscribe(
        "topic", lambda p: received.append(p["seq"]), criticality=HandlerCriticality.CRITICAL
    )
    for i in range(5):
        await bus.publish("topic", {"seq": i})
    del bus
    restarted = InProcessEventBus()
    restarted.subscribe(
        "topic", lambda p: received.append(p["seq"]), criticality=HandlerCriticality.CRITICAL
    )
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    return len(received)
