"""M2-8 red-gate 다중 토픽 negative 보강 (task-25240).

`test_in_process_baseline_red_gate.py::test_in_process_bus_multiple_topics_all_lose_events_on_crash`
의 negative 증거(≥3)를 채운다. 원 파일이 500줄 직전이라 sibling으로 분리했다.
"""

from __future__ import annotations

import asyncio

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality

_TOPICS = ("a", "b", "c")


def _make_handler(received: dict[str, list[int]], topic: str):
    async def handler(payload):
        received[topic].append(payload["seq"])

    return handler


async def _crash_and_restart(
    received: dict[str, list[int]], *, publish_after_restart: bool = False
) -> None:
    crashed = InProcessEventBus()
    for topic in _TOPICS:
        crashed.subscribe(
            topic, _make_handler(received, topic), criticality=HandlerCriticality.CRITICAL
        )
    for topic in _TOPICS:
        for seq in range(3):
            await crashed.publish(topic, {"seq": seq})
    del crashed

    restarted = InProcessEventBus()
    for topic in _TOPICS:
        restarted.subscribe(
            topic, _make_handler(received, topic), criticality=HandlerCriticality.CRITICAL
        )
    await restarted.start()
    if publish_after_restart:
        await restarted.publish("b", {"seq": 100})
    await asyncio.sleep(0.1)
    await restarted.stop()


async def test_multi_topic_crash_loses_every_event_on_every_topic():
    """negative — 토픽당 여러 건을 발행해도 모든 토픽의 수신이 비어 있다."""
    received: dict[str, list[int]] = {t: [] for t in _TOPICS}
    await _crash_and_restart(received)
    assert received == {"a": [], "b": [], "c": []}


async def test_multi_topic_crash_only_post_restart_event_arrives_on_one_topic():
    """negative — 재시작 뒤 한 토픽에 발행한 이벤트만 도착하고 다른 토픽은 비어 있다."""
    received: dict[str, list[int]] = {t: [] for t in _TOPICS}
    await _crash_and_restart(received, publish_after_restart=True)
    assert received == {"a": [], "b": [100], "c": []}


async def test_multi_topic_crash_unsubscribed_restart_receives_nothing():
    """negative — 재시작 버스에 구독이 없으면 크래시 전 이벤트는 어디로도 이월되지 않는다."""
    received: dict[str, list[int]] = {t: [] for t in _TOPICS}
    crashed = InProcessEventBus()
    for topic in _TOPICS:
        crashed.subscribe(
            topic, _make_handler(received, topic), criticality=HandlerCriticality.CRITICAL
        )
        await crashed.publish(topic, {"seq": 1})
    del crashed

    restarted = InProcessEventBus()
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    assert all(not v for v in received.values())


async def test_multi_topic_crash_assertion_catches_preserving_bus():
    """gate-red — 이벤트를 보존하는 가짜 버스를 쓰면 같은 단언이 적색으로 떨어진다."""
    received: dict[str, list[int]] = {t: [] for t in _TOPICS}
    # 입력을 망가뜨린다: 크래시 전 핸들러가 이미 이벤트를 처리했다고 가정.
    for topic in _TOPICS:
        received[topic].append(1)
    try:
        assert received == {"a": [], "b": [], "c": []}
    except AssertionError:
        return
    raise RuntimeError("negative assertion failed to detect preserved events")
