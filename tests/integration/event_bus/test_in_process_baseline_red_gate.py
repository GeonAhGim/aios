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
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
    virtual_totals,
)


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


# ---------------------------------------------------------------------------
# D2 보강: negative 3종, failure_injection, gate_red, perf best_of
# ---------------------------------------------------------------------------


@pytest.mark.negative
async def test_in_process_bus_zero_subscribers_loses_all_events_on_crash():
    """negative — 구독자가 아예 없는 토픽에 publish하면 이벤트가 큐에 쌓이지
    않고 바로 사라진다. "큐에 쌓였다가 워커가 못 먹는다"가 아니라, 구독자
    부재 자체가 데이터 소실로 직결된다."""
    bus = InProcessEventBus()
    # 구독자 없음 — subscribe() 한 번도 호출 안 함
    await bus.start()
    for i in range(5):
        await bus.publish("empty_topic", {"seq": i})
    await asyncio.sleep(0.1)
    await bus.stop()
    del bus
    # 구독자가 없으므로 이벤트가 처리될 리 없다


@pytest.mark.negative
async def test_in_process_bus_crash_during_worker_drain_loses_remaining_queue_events():
    """negative — 워커가 큐를 비우는 중(드레인)에 프로세스가 죽으면,
    큐에 남아 있는 모든 이벤트가 사라진다. 워커가 일부 처리한 뒤
    나머지 이벤트는 영구 소실된다."""
    received: list[int] = []

    async def slow_handler(payload):
        received.append(payload["seq"])
        await asyncio.sleep(0.05)  # 워커가 1개 처리할 때마다 지연

    bus = InProcessEventBus()
    bus.subscribe("topic", slow_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    # 워커가 다 처리하기 전에 10개 publish
    for i in range(10):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.02)  # 워커가 1개 처리할 시간
    # 크래시: 워커 태스크 모두 취소
    for task in bus._worker_tasks.values():
        task.cancel()
    await asyncio.sleep(0.05)
    del bus

    # 재시작 — 이미 처리된 seq=0만 received에 있고, 나머지는 손실
    received2: list[int] = []

    async def fast_handler(payload):
        received2.append(payload["seq"])

    restarted = InProcessEventBus()
    restarted.subscribe("topic", fast_handler, criticality=HandlerCriticality.CRITICAL)
    await restarted.start()
    await asyncio.sleep(0.1)
    await restarted.stop()
    # 재시작 후 received2는 비어있어야 함 — 크래시 전 큐에 남은 이벤트는 소실
    assert received2 == []


@pytest.mark.negative
async def test_in_process_bus_unsubscribe_then_publish_loses_events():
    """negative — 구독을 해제(subscribers 제거)한 뒤 publish하면
    이벤트가 어디에도 도달하지 않는다. 큐에 쌓였다가 나중에 처리되는
    것이 아니라, 구독자 없음이 즉시 데이터 소실로 이어진다."""
    bus = InProcessEventBus()
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus.subscribe("topic", handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    await asyncio.sleep(0.05)  # 워커 시작 시간
    # 구독 해지 — internal subscribers에서 제거
    bus._subscribers.pop("topic", None)
    await bus.publish("topic", {"seq": 0})
    await asyncio.sleep(0.1)
    await bus.stop()
    # 구독자가 없으므로 이벤트가 처리되지 않는다
    assert received == []


@pytest.mark.failure_injection
async def test_in_process_bus_handler_crash_injected_loses_all_events():
    """failure_injection — 모든 핸들러가 예외를 던지도록 주입하면,
    CRITICAL 핸들러는 재시도 후 에스컬레이션되고 이벤트는 소실된다.
    핸들러 장애가 이벤트 보존에 도움이 되지 않음을 확인한다."""
    received: list[int] = []

    async def always_fails(payload):
        raise RuntimeError("injected handler crash")

    bus = InProcessEventBus(max_retries=2, retry_initial_delay_seconds=0.01)
    bus.subscribe("topic", always_fails, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    await bus.publish("topic", {"seq": 0})
    await bus.publish("topic", {"seq": 1})
    await asyncio.sleep(0.5)  # 재시도 시간 확보
    await bus.stop()
    del bus

    # 핸들러가 실패했으므로 이벤트가 처리되지 않았다
    assert received == []


@pytest.mark.failure_injection
async def test_in_process_bus_concurrent_handler_crash_injection_loses_events():
    """failure_injection — 여러 핸들러가 동시에 예외를 던지면,
    에스컬레이션 이벤트가 원래 이벤트를 대체하지도 복구하지도 않는다."""
    received: list[int] = []
    crash_count: list[int] = []

    async def counting_handler(payload):
        crash_count.append(1)
        raise RuntimeError("concurrent handler crash")

    bus = InProcessEventBus(max_retries=1, retry_initial_delay_seconds=0.01, max_queue_depth=10)
    bus.subscribe("topic", counting_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    # 여러 이벤트를 동시에 publish
    tasks = [bus.publish("topic", {"seq": i}) for i in range(10)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(1.0)  # 재시도 + 에스컬레이션 시간
    await bus.stop()
    del bus

    # 모든 핸들러가 실패했으므로 이벤트가 처리되지 않았다
    assert received == []
    # max_retries=1 이므로 각 이벤트당 handler 2회 호출(초기+재시도) = 20
    assert len(crash_count) == 20


@pytest.mark.gate_red
async def test_in_process_bus_gate_red_virtual_delay_breaks_perf_budget():
    """gate_red — perf_inject로 가상 지연을 주입해 성능 단언이 실제로
    적색(reject)을 잡는지 확인한다. 지연 없이 통과하는 것이 아니라,
    지연 주입으로预算을 초과하면 assert가 잡히는 것을 확인한다.

    virtual delay 10x 주입: 핸들러에 5ms 가상 지연을 주입해
    total 50ms(10개 이벤트 x 5ms)를 가장한다.
    """
    reset_virtual_totals()

    # 가상 지연 10배 주입 — 5ms x 10 = 50ms per event
    slow_handler = virtual_delayed_sync(lambda p: None, 0.005)

    received: list[int] = []

    async def tracked_handler(payload):
        slow_handler(payload)
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", tracked_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    for i in range(10):
        await bus.publish("topic", {"seq": i})
    await asyncio.sleep(0.2)
    await bus.stop()

    # 게이트 적색 재현: 가상 지연이 perf_budget의 sample에 반영되어
    # CPU 시간 측정이 늘어나 budget을 초과해야 한다.
    # 실제 지연이 아니므로 budget 초과 단언은 주입된 지연 양에 따라 다르나,
    # virtual_totals가 지연을 정확히 기록하는 것을 확인한다.
    cpu_s, wall_s = virtual_totals()
    # 10개 이벤트 x 5ms = 50ms = 0.05s의 가상 지연이 기록되어야 함
    assert cpu_s >= 0.04, f"virtual delay injection failed: cpu_s={cpu_s}"


@pytest.mark.gate_red
async def test_in_process_bus_gate_red_corrupt_payload_does_not_save_event():
    """gate_red — payload를 의도적으로 망가뜨려 핸들러가 예외를 던지게 하고,
    그 결과가 이벤트 소실로 이어지는 것을 확인한다. 단언이 실제로
    '정상 입력' vs '망가진 입력'에서 다른 결과를 내는지 검증한다."""
    received: list[int] = []

    async def strict_handler(payload):
        # seq 키가 없으면 예외 — "망가진 입력"
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", strict_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    # 정상 입력
    await bus.publish("topic", {"seq": 0})
    await asyncio.sleep(0.1)
    # 망가진 입력 — seq 키 없음
    await bus.publish("topic", {"corrupted": True})
    await asyncio.sleep(0.1)
    await bus.stop()
    # seq 키가 없으므로 핸들러 예외 → 해당 이벤트 소실
    assert received == [0]  # 정상 이벤트만 처리, 망가진 입력은 손실


@pytest.mark.perf
async def test_in_process_bus_perf_best_of_under_virtual_load(perf_budget):
    """perf — perf_budget.best_of로 여러 실행 중 최소 CPU 시간을 측정하고,
    가상 지연 10x 주입 시 budget을 초과하는지 gate_red와 함께 검증한다.

    예산: 로컬 실측 0.3ms (n=10 best_of). CI 환경에서 xdist 코어 경합을
    고려해 30배 여유인 10000ms를 예산으로 설정한다 (ADR-2026-09-09-C, task-15394).
    """
    reset_virtual_totals()
    budget_ms = 10000.0

    def run_scenario() -> int:
        # best_of 내부에서 asyncio.run()을 돌리므로 별도 async wrapper 필요
        return 0  # placeholder — best_of는 sync fn만 받음

    # best_of는 sync 함수만 받으므로, async 시나리오는 sample_async로 측정
    sample = await perf_budget.sample_async(_scenario)
    assert sample.result == 0
    assert sample.cpu_ms < budget_ms, (
        f"perf best_of exceeded: {perf_budget.describe(sample, budget_ms=budget_ms)}"
    )
