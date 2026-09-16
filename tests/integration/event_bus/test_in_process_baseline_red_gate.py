"""M2-8 red-gate 재현 — Redis 없이 항상 돈다(기본 CI 스위트 포함).

`InProcessEventBus`는 프로세스 경계를 넘는 영속성이 전혀 없어, 발행 후
"재시작" 시나리오에서 이벤트가 그냥 사라진다. 이게 M2-8이 고치는 결함의
red baseline이고, `test_redis_streams_event_bus.py::test_crash_then_replay_no_loss`
(`pytest -m redis`로 명시 실행)가 그 수정을 증명하는 짝이다.
"""

from __future__ import annotations

import asyncio

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
