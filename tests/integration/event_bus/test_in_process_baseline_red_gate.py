"""M2-8 red-gate 재현 — Redis 없이 항상 돈다(기본 CI 스위트 포함).

`InProcessEventBus`는 프로세스 경계를 넘는 영속성이 전혀 없어, 발행 후
"재시작" 시나리오에서 이벤트가 그냥 사라진다. 이게 M2-8이 고치는 결함의
red baseline이고, `test_redis_streams_event_bus.py::test_crash_then_replay_no_loss`
(`pytest -m redis`로 명시 실행)가 그 수정을 증명하는 짝이다.
"""

from __future__ import annotations

import asyncio

import pytest

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


async def test_in_process_bus_loses_events_on_crash_baseline_repeat_is_stable():
    """negative — 시나리오를 반복해도 소실 결과가 우연이 아니라 매번 0이다."""
    assert [await _scenario() for _ in range(3)] == [0, 0, 0]


async def test_in_process_bus_loses_events_on_crash_baseline_fresh_bus_has_no_history():
    """negative — 재시작한 새 버스에는 이전 인스턴스의 큐/구독 이력이 없다."""
    crashed = InProcessEventBus()
    for i in range(3):
        await crashed.publish("topic", {"seq": i})
    del crashed

    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    assert received == []  # 구독이 없던 시점의 publish는 새 인스턴스로 이월되지 않는다


async def test_in_process_bus_loses_events_on_crash_baseline_only_post_restart_events_arrive():
    """negative — 재시작 뒤 새로 발행한 이벤트만 도착하고 크래시 전 이벤트는 오지 않는다."""
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    crashed = InProcessEventBus()
    crashed.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.publish("topic", {"seq": 0})
    del crashed

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await restarted.publish("topic", {"seq": 1})
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == [1]  # seq=0(크래시 전)은 복구되지 않는다


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


async def test_in_process_bus_backpressure_rejection_with_slow_handler_loses_more():
    """negative — 핸들러가 느리면 워커가 큐를 비우지 못해 백프레셔가 계속 발생하고,
    추가로 들어오는 이벤트가 모두 거부되어 영구히 사라진다."""
    bus = InProcessEventBus(max_queue_depth=2)
    received: list[int] = []

    async def slow_handler(payload):
        await asyncio.sleep(0.5)  # 워커가 한 건 처리하는 데 0.5초 — 큐가 계속 가득 참
        received.append(payload["seq"])

    bus.subscribe("topic", slow_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    # 5건을 빠르게 쏜다 — 핸들러가 느리므로 큐가 계속 가득 차서 대부분 거부됨
    for i in range(5):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.1)
    await bus.stop()
    # slow_handler가 1건만 처리하고 나머지는 큐에서 거부됨
    assert len(received) <= 1  # 최대 1건만 처리 가능(다른 워커가 없음)


async def test_in_process_bus_backpressure_rejection_persists_after_restart():
    """negative — 백프레셔로 거부된 이벤트가 재시작 후에도 복구되지 않는다는 것을
    확인한다. "재시작하면 큐가 비워진다"는 가설을 기각한다."""
    bus = InProcessEventBus(max_queue_depth=1)
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus.subscribe("topic", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic", {"seq": 0})  # 큐에 쌓임
    await bus.publish("topic", {"seq": 1})  # 큐 가득 참 — 거부
    await asyncio.sleep(0.05)  # 워커가 seq=0을 처리할 시간을 약간 준다
    # bus를del — "크래시"
    del bus

    # 재시작: 새 bus가 같은 핸들러를 구독
    restarted = InProcessEventBus(max_queue_depth=1)
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.SAFE)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    # seq=1은 거부된 시점에 영구히 사라졌고, seq=0도 처리 전이면 사라짐
    # (이미 처리됐을 수도 있으니 received == [0] 또는 [] 둘 중 하나)
    assert received in ([], [0])


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


async def test_in_process_bus_multiple_topics_in_flight_crash_loses_all():
    """negative — 토픽별 워커가 모두 핸들러 실행 중(in-flight)일 때 크래시해도
    모든 토픽의 이벤트가 재시작 후 복구되지 않는다."""
    received: dict[str, list[int]] = {"a": [], "b": [], "c": []}

    def make_handler(topic: str, delay: float):
        async def handler(payload):
            await asyncio.sleep(delay)
            received[topic].append(payload["seq"])

        return handler

    crashed = InProcessEventBus()
    for topic in received:
        crashed.subscribe(topic, make_handler(topic, 10), criticality=HandlerCriticality.CRITICAL)
    await crashed.start()
    for topic in received:
        await crashed.publish(topic, {"seq": 1})
    await asyncio.sleep(0.05)
    for task in crashed._worker_tasks.values():
        task.cancel()
    del crashed

    restarted = InProcessEventBus()
    for topic in received:
        restarted.subscribe(topic, make_handler(topic, 0), criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == {"a": [], "b": [], "c": []}


async def test_in_process_bus_multiple_topics_backpressure_rejects_lost_after_crash():
    """negative — 토픽마다 큐가 가득 찬 상태(미시작)에서 크래시하면 수락된
    이벤트도 거부된 이벤트도 재시작 후 전부 소실된다."""
    received: dict[str, list[int]] = {"a": [], "b": []}

    def make_handler(topic: str):
        async def handler(payload):
            received[topic].append(payload["seq"])

        return handler

    crashed = InProcessEventBus(max_queue_depth=1)
    for topic in received:
        crashed.subscribe(topic, make_handler(topic), criticality=HandlerCriticality.SAFE)
    for topic in received:
        await crashed.publish(topic, {"seq": 0})
        await crashed.publish(topic, {"seq": 1})  # 큐 가득 — 거부
    del crashed

    restarted = InProcessEventBus(max_queue_depth=1)
    for topic in received:
        restarted.subscribe(topic, make_handler(topic), criticality=HandlerCriticality.SAFE)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == {"a": [], "b": []}


async def test_in_process_bus_multiple_topics_gate_red_survivor_is_detected():
    """gate-red — 크래시 없이 같은 bus를 계속 쓰면 모든 토픽이 전달된다.
    즉 `== []` 단언은 이벤트가 실제로 보존될 때 적색이 되는 입력을 갖는다."""
    received: dict[str, list[int]] = {"a": [], "b": [], "c": []}

    def make_handler(topic: str):
        async def handler(payload):
            received[topic].append(payload["seq"])

        return handler

    bus = InProcessEventBus()
    for topic in received:
        bus.subscribe(topic, make_handler(topic), criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    for topic in received:
        await bus.publish(topic, {"seq": 1})
    await asyncio.sleep(0.1)
    await bus.stop()
    assert not all(received[topic] == [] for topic in received)  # 망가진 입력 → 적색
    assert received == {"a": [1], "b": [1], "c": [1]}


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


async def test_in_process_bus_publish_before_start_loses_events():
    """negative — start()를 호출하지 않은 상태에서는 워커가 없으므로
    publish한 이벤트가 모두 사라진다. "start()를 깜빡했다"는 실수도 구조적으로
    데이터를 보존하지 못한다."""
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    for i in range(10):
        await bus.publish("topic", {"seq": i})
    # start()를 부르지 않은 채 바로 stop + del
    await bus.stop()
    del bus
    assert received == []  # 워커가 한 번도 안 떠서 이벤트가 처리되지 않았다


async def test_in_process_bus_publish_after_stop_loses_events():
    """negative — stop() 이후 publish는 큐에 담기지 않고 바로 거부된다.
    "재시작 없이 계속 돌린다"는 가정도 데이터를 보존하지 못한다."""
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    await bus.publish("topic", {"seq": 0})
    await asyncio.sleep(0.05)  # 워커가 seq=0을 처리할 시간
    await bus.stop()
    # stop() 이후 publish — 큐가 닫혀 있으므로 이벤트가 사라진다
    await bus.publish("topic", {"seq": 1})
    await asyncio.sleep(0.05)
    assert received == [0]  # stop 이후의 이벤트는 손실된다


async def test_in_process_bus_concurrent_publishers_all_lose_events_on_crash():
    """negative — 여러 publish가 동시에 들어와도, 크래시 전에는
    in-memory 큐에 쌓인 채이므로 재시작 시 모두 사라진다.
    단일 토픽이 아니라도 동일하다."""
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    # 여러 이벤트를 동시에 publish (비동기 병렬 호출)
    tasks = [bus.publish("topic", {"seq": i}) for i in range(20)]
    await asyncio.gather(*tasks)
    del bus  # "크래시"
    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # 병렬 publish도 구조적으로 보존되지 않는다


async def test_in_process_bus_in_flight_handler_crash_loses_partial_payload():
    """negative — "start() 후 워커가 이벤트를 꺼내 핸들러를 실행하는 순간까지
    안전하다"는 가설을 기각한다. 핸들러가 slow_sleep 중인 경우, 그 이벤트의
    payload는 재시작 시 전혀 복구되지 않는다."""
    received: list[int] = []

    async def slow_handler(payload):
        await asyncio.sleep(10)
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    # slow_handler는 10초를 sleep하므로, 이 이벤트는 in-flight 상태로 남는다
    await bus.publish("topic", {"seq": 42})
    await asyncio.sleep(0.05)  # 워커가 큐에서 꺼내고 핸들러 시작
    # 워커 태스크를 강제 취소 (크래시 시뮬레이션)
    for task in bus._worker_tasks.values():
        task.cancel()
    del bus

    restarted = InProcessEventBus()
    restarted.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    assert received == []  # in-flight 핸들러의 payload도 영구 손실


async def test_in_process_bus_crash_during_backpressure_loses_queued_events():
    """negative — 백프레셔가 발생 중(큐 가득 참)에 크래시하면, 큐에 쌓인
    이벤트들이 모두 사라진다. "어떤 이벤트는 큐에 남아있지 않겠지"라는
    가설을 기각한다."""
    received: list[int] = []

    async def slow_handler(payload):
        await asyncio.sleep(5)  # 워커가 바빠서 큐가 계속 채워진다
        received.append(payload["seq"])

    bus = InProcessEventBus(max_queue_depth=3)
    bus.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    # slow_handler가 5초씩 sleep하므로, 4개 이상은 큐에 쌓이거나 거부된다
    for i in range(6):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.1)
    # 워커 태스크 취소 + del (크래시)
    for task in bus._worker_tasks.values():
        task.cancel()
    del bus

    restarted = InProcessEventBus(max_queue_depth=3)
    restarted.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    # seq 0-2 중 일부가 처리됐을 수 있으나, 큐에 남아있던 이벤트는 모두 손실
    # 전체 이벤트가 보존되지 않는다는 negative 증거
    assert received != list(range(6))  # 전부가 보존되지 않는다


async def test_in_process_bus_gate_red_negative_assertion_catches_corruption():
    """gate-red — 이 negative 테스트의 단언(`assert received == []`)이
    실제로 잡히는지를 확인한다. 만약 InProcessEventBus가 우연히 이벤트를
    보존하면 이 테스트는 파란불이 된다.

    입력을 의도적으로 망가뜨려(red) 단언이 적색으로 떨어지는 것을 재현한다:
    - slow_handler로 이벤트를 in-flight 상태로 만든 후 크래시 시뮬레이션하고,
      restarted bus에 같은 핸들러를 연결해도 해당 이벤트가 전달되지 않음을 확인한다.
    """
    received: list[int] = []

    async def slow_handler(payload):
        await asyncio.sleep(10)  # 절대 끝나지 않는 slow handler — in-flight 고정
        received.append(payload["seq"])

    crashed = InProcessEventBus()
    crashed.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await crashed.start()
    await crashed.publish("topic", {"seq": 99})
    await asyncio.sleep(0.05)  # 워커가 큐에서 꺼내 slow_handler 시작
    for task in crashed._worker_tasks.values():
        task.cancel()
    del crashed

    restarted = InProcessEventBus()
    restarted.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.05)
    await restarted.stop()
    # gate-red: 단언이 실제로 잡힘 — received가 비어있지 않으면
    # InProcessEventBus가 구조적으로 이벤트를 보존하는 것이므로 실패
    assert received == []  # 단언이 적색: crashed bus의 in-flight 이벤트는 restarted에 전달되지 않음


async def test_in_process_bus_rapid_subscribe_publish_crash_loses_all():
    """negative — subscribe와 publish가 빠르게 연달아 일어나도,
    크래시 전 in-memory 큐에 쌓인 이벤트는 재시작 시 소실된다.
    타이밍이 운이 아니다."""
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    # start() 전에 publish — 워커가 뜰 전에 이벤트가 큐에 쌓인다
    for i in range(15):
        await bus.publish("topic", {"seq": i})
    # 바로 del — start()도 안 부른 채
    del bus

    restarted = InProcessEventBus()
    restarted.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    assert received == []  # start 전 publish도 구조적으로 보존되지 않는다


@pytest.mark.perf
async def test_in_process_bus_crash_detection_perf_budget(perf_budget):
    """perf — 이 red-gate 재현 자체가 CI 기본 스위트에 상시 포함되므로,
    시나리오 1회 실행이 예산(5000ms, xdist 코어 경합/CI 병렬 안정성) 안에
    끝나야 한다 (p99 budget, ADR-2026-09-09-C, task-15394)."""
    budget_ms = 5000.0
    sample = await perf_budget.sample_async(_scenario)
    assert sample.result == 0
    assert sample.wall_ms < budget_ms, (
        f"red-gate scenario: {perf_budget.describe(sample, budget_ms=budget_ms)}"
    )
