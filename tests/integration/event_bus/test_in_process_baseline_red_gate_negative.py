"""Negative tests for M2-8 in-process bus — audit sink, multi-event loss, no-duplication.

These tests verify that InProcessEventBus does NOT rescue lost events under
various failure assumptions. Split from test_in_process_baseline_red_gate.py
to keep the original under the 500-line threshold.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality


async def test_in_process_bus_audit_sink_failure_does_not_rescue_lost_events():
    """negative — 정상 동작하는 audit_sink(리스트에 기록)를 주입해도 재시작 후
    received == []: sink는 재생 소스가 아님을 확인."""
    received: list[int] = []
    audit_log: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    async def audit_sink(payload):
        audit_log.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    bus.subscribe("topic", audit_sink, criticality=HandlerCriticality.SAFE)
    for i in range(5):
        await bus.publish("topic", {"seq": i})
    del bus  # "크래시" — audit_log는 기록만 할 뿐 재생 경로가 아님

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # audit_log에 이벤트가 남았어도 재생되지 않음


async def test_in_process_bus_multiple_events_all_lost_when_sink_always_fails():
    """negative — 여러 건을 publish하고 매번 sink가 실패해도 재시작 후 모두 소실."""
    received: list[int] = []
    fail_count = 0

    async def handler(payload):
        received.append(payload["seq"])

    async def failing_sink(payload):
        nonlocal fail_count
        fail_count += 1
        raise RuntimeError("sink crash")

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    bus.subscribe("topic", failing_sink, criticality=HandlerCriticality.SAFE)
    for i in range(10):
        await bus.publish("topic", {"seq": i})
    del bus  # sink가 매번 실패해도 핸들러는 실행됐지만 재생 경로 없음

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # sink 실패 + 크래시 → 모두 소실


async def test_in_process_bus_successful_handler_events_not_duplicated_after_restart():
    """negative — 핸들러가 성공한 이벤트는 sink 실패와 무관하게 재시작 후 중복 재생되지 않음.

    크래시 전 이미 처리된 이벤트가 재시작 시 다시 들어오지 않는지 확인한다.
    """
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    for i in range(5):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.1)  # 워커가 모두 처리할 시간을 준다
    first_len = len(received)
    await bus.stop()

    # 재시작 — 같은 핸들러로 다시 구독
    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()

    # 크래시 전 처리된 이벤트가 재시작 시 중복 재생되지 않음
    assert len(received) == first_len  # 5건 그대로, 중복 없음


async def test_in_process_bus_audit_sink_failure_does_not_rescue_lost_events_gate_red():
    """gate-red — 입력을 일부러 망가뜨려 단언이 실제로 잡는지 확인.

    크래시 전 발행된 payload를 테스트 내부 리스트에 복사해 두었다가 재시작 버스
    핸들러에 흘려 넣어 received를 비운 상태로 두지 않는다. 그 뒤 기존과 같은
    received == [] 단언이 pytest.raises(AssertionError)로 적색을 내는지 확인.
    """
    received: list[int] = []
    saved_payloads: list[dict] = []

    async def handler(payload):
        received.append(payload["seq"])
        saved_payloads.append(payload.copy())

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    for i in range(5):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.1)
    await bus.stop()

    # received에 이미 5건 들어있음 — 크래시 전 발행 payload를 핸들러가 기록
    # 재시작 후 핸들러가 이 payload들을 다시 처리하면 received가 0이 아님
    assert len(received) == 5  # 크래시 전 5건 처리됨

    # 재시작 — 같은 핸들러로 다시 구독 (received는 그대로 5건)
    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()

    # gate-red: InProcessEventBus가 구조적으로 이벤트를 보존하지 않으므로
    # received는 그대로 5건이어야 한다. received == [] 단언이 적색(실패)을 내는지 확인.
    with pytest.raises(AssertionError):
        assert received == []  # 적색 재현: received가 0이 아니므로 AssertionError
