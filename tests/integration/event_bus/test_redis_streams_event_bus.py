"""M2-8 Phase2 DoD — RedisStreamsEventBus.

Spec: ADR-2026-09-09-B(M2-8). DoD: 강제 종료 후 재생 무손실, 인스턴스 2개
동시 소비 중복 0, in_process는 테스트용으로 유지(그 자체를 대조군으로 쓴다).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal

import pytest

from src.core.event_bus.policy import HandlerCriticality
from src.core.event_bus.redis_streams import (
    HANDLER_ESCALATED_TOPIC,
    RedisStreamsEventBus,
)

# 실 Redis가 필요한 통합테스트 — 기본 실행에서 제외된다(pyproject.toml
# addopts, task-2624 conftest.py 참고). 명시 실행: `pytest -m redis`.
pytestmark = [pytest.mark.timeout(60), pytest.mark.redis]


def _topic(name: str) -> str:
    return f"test.m2_8.{name}.{uuid.uuid4().hex[:8]}"


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"timeout({timeout}s) waiting for condition")


# ----------------------------------------------------------------------
# 기본 동작
# ----------------------------------------------------------------------


async def test_publish_subscribe_roundtrip(redis_url):
    topic = _topic("roundtrip")
    received: list[dict] = []

    async def handler(payload):
        received.append(payload)

    bus = RedisStreamsEventBus(redis_url=redis_url, consumer_group=f"g-{uuid.uuid4().hex[:6]}")
    bus.subscribe(topic, handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    try:
        await bus.publish(topic, {"amount": str(Decimal("1.50")), "n": 1})
        await _wait_until(lambda: len(received) == 1)
    finally:
        await bus.stop()

    assert received == [{"amount": "1.50", "n": 1}]


async def test_preserves_order_for_single_consumer(redis_url):
    topic = _topic("order")
    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    bus = RedisStreamsEventBus(redis_url=redis_url, consumer_group=f"g-{uuid.uuid4().hex[:6]}")
    bus.subscribe(topic, handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    try:
        for i in range(30):
            await bus.publish(topic, {"seq": i})
        await _wait_until(lambda: len(received) == 30)
    finally:
        await bus.stop()

    assert received == list(range(30))


# ----------------------------------------------------------------------
# DoD 1 — 강제 종료 후 재생 무손실
# ----------------------------------------------------------------------


async def test_crash_then_replay_no_loss(redis_url, redis_client):
    """이전 컨슈머가 ACK 없이 죽어도(PEL에만 남은 상태), 재시작한 컨슈머가
    XAUTOCLAIM으로 이어받아 전부 처리한다 — 이벤트 유실 0."""
    topic = _topic("crash_replay")
    group = f"g-{uuid.uuid4().hex[:6]}"
    n = 20

    publisher = RedisStreamsEventBus(redis_url=redis_url, consumer_group=group)
    for i in range(n):
        await publisher.publish(topic, {"seq": i})
    await publisher.stop()  # publish 전용 — 워커를 띄운 적 없으니 close만

    stream = RedisStreamsEventBus.stream_key(topic)
    # "죽은 컨슈머" — 그룹을 만들고 메시지를 읽기만 한 뒤 ACK도 stop()도
    # 하지 않고 그대로 버려서(강제 종료 시뮬레이션) PEL에 n건을 남긴다.
    await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    dead = await redis_client.xreadgroup(group, "dead-consumer", {stream: ">"}, count=n)
    assert sum(len(entries) for _s, entries in dead) == n

    received: list[int] = []

    async def handler(payload):
        received.append(payload["seq"])

    survivor = RedisStreamsEventBus(
        redis_url=redis_url,
        consumer_group=group,
        consumer_name="survivor-consumer",
        claim_min_idle_ms=50,
        block_ms=100,
    )
    survivor.subscribe(topic, handler, criticality=HandlerCriticality.CRITICAL)
    await survivor.start()
    try:
        await asyncio.sleep(0.2)  # PEL idle 시간이 claim_min_idle_ms를 넘도록
        await _wait_until(lambda: len(received) == n, timeout=10.0)
    finally:
        await survivor.stop()

    assert sorted(received) == list(range(n))
    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0


# ----------------------------------------------------------------------
# DoD 2 — 다중 인스턴스 소비자 그룹, 중복 0
# ----------------------------------------------------------------------


async def test_two_instances_consumer_group_no_duplicates(redis_url):
    topic = _topic("multi_instance")
    group = f"g-{uuid.uuid4().hex[:6]}"
    n = 100
    processed: list[int] = []
    lock = asyncio.Lock()

    async def make_handler():
        async def handler(payload):
            async with lock:
                processed.append(payload["seq"])

        return handler

    bus_a = RedisStreamsEventBus(
        redis_url=redis_url, consumer_group=group, consumer_name="instance-a", block_ms=100
    )
    bus_b = RedisStreamsEventBus(
        redis_url=redis_url, consumer_group=group, consumer_name="instance-b", block_ms=100
    )
    handler_a = await make_handler()
    handler_b = await make_handler()
    bus_a.subscribe(topic, handler_a, criticality=HandlerCriticality.CRITICAL)
    bus_b.subscribe(topic, handler_b, criticality=HandlerCriticality.CRITICAL)

    for i in range(n):
        await bus_a.publish(topic, {"seq": i})

    await bus_a.start()
    await bus_b.start()
    try:
        await _wait_until(lambda: len(processed) == n, timeout=15.0)
        await asyncio.sleep(0.3)  # 그 이상 더 들어오는(중복) 게 없는지 잠깐 더 관찰
    finally:
        await bus_a.stop()
        await bus_b.stop()

    assert len(processed) == n, f"손실/초과 발생: {len(processed)} != {n}"
    assert sorted(processed) == list(range(n)), "중복 또는 누락 발생"


# ----------------------------------------------------------------------
# 멱등 소비자
# ----------------------------------------------------------------------


async def test_idempotent_consumer_skips_duplicate_delivery(redis_url, redis_client):
    """발행측 재시도 등으로 같은 event_id를 담은 엔트리가 스트림에 두 번
    들어와도(entry_id는 다르지만 envelope의 event_id는 동일), handler는
    한 번만 불린다."""
    topic = _topic("idempotent")
    group = f"g-{uuid.uuid4().hex[:6]}"
    call_count = 0

    async def handler(payload):
        nonlocal call_count
        call_count += 1

    bus = RedisStreamsEventBus(redis_url=redis_url, consumer_group=group, block_ms=100)
    bus.subscribe(topic, handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    try:
        await bus.publish(topic, {"n": 1})
        await _wait_until(lambda: call_count == 1)

        stream = RedisStreamsEventBus.stream_key(topic)
        entries = await redis_client.xrange(stream, "-", "+")
        assert len(entries) == 1
        _entry_id, fields = entries[0]
        # 같은 envelope(= 같은 event_id)를 새 entry_id로 다시 발행 — 발행측
        # at-least-once 재시도를 시뮬레이션.
        await redis_client.xadd(stream, {"envelope": fields["envelope"]})

        await asyncio.sleep(0.5)  # handler가 다시 불렸다면 이 사이 call_count가 늘었을 시간
    finally:
        await bus.stop()

    assert call_count == 1, "멱등성 위반 — 같은 event_id에 handler가 두 번 불렸다"


# ----------------------------------------------------------------------
# Negative tests (>=3)
# ----------------------------------------------------------------------


async def test_publish_rejects_non_json_serializable_payload(redis_url):
    topic = _topic("bad_payload")
    bus = RedisStreamsEventBus(redis_url=redis_url)
    try:
        with pytest.raises(TypeError):
            await bus.publish(topic, {"bad": {1, 2, 3}})  # set은 JSON 직렬화 불가
    finally:
        await bus.stop()


async def test_safe_handler_error_does_not_stop_worker_or_other_handlers(redis_url):
    topic = _topic("safe_error")
    audit_records: list[dict] = []
    second_handler_calls: list[dict] = []
    call_count = 0

    async def failing_handler(payload):
        nonlocal call_count
        call_count += 1
        raise ValueError("의도적 실패")

    async def other_handler(payload):
        second_handler_calls.append(payload)

    async def audit_sink(record):
        audit_records.append(record)

    bus = RedisStreamsEventBus(redis_url=redis_url, audit_sink=audit_sink, block_ms=100)
    bus.subscribe(topic, failing_handler, criticality=HandlerCriticality.SAFE)
    bus.subscribe(topic, other_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    try:
        await bus.publish(topic, {"n": 1})
        await _wait_until(lambda: len(second_handler_calls) == 1)
        # 워커가 죽지 않았는지 — 다음 이벤트도 정상 처리되는지 확인
        await bus.publish(topic, {"n": 2})
        await _wait_until(lambda: call_count == 2)
    finally:
        await bus.stop()

    assert len(second_handler_calls) == 2
    assert any(r["action_type"] == "handler_error_safe" for r in audit_records)


async def test_critical_handler_exhausts_retries_and_escalates(redis_url, redis_client):
    topic = _topic("critical_escalate")
    group = f"g-{uuid.uuid4().hex[:6]}"
    escalations: list[dict] = []

    async def always_fails(payload):
        raise RuntimeError("영구 실패")

    async def escalation_handler(payload):
        escalations.append(payload)

    bus = RedisStreamsEventBus(
        redis_url=redis_url,
        consumer_group=group,
        max_retries=1,
        retry_initial_delay_seconds=0.01,
        block_ms=100,
    )
    bus.subscribe(topic, always_fails, criticality=HandlerCriticality.CRITICAL)
    bus.subscribe(HANDLER_ESCALATED_TOPIC, escalation_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    try:
        await bus.publish(topic, {"n": 1})
        await _wait_until(lambda: len(escalations) == 1, timeout=10.0)
    finally:
        await bus.stop()

    assert escalations[0]["topic"] == topic
    assert escalations[0]["retries"] == 1
    stream = RedisStreamsEventBus.stream_key(topic)
    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0, "재시도 소진 후에도 ACK되어 큐가 막히지 않아야 한다"


async def test_malformed_envelope_entry_is_skipped_and_acked(redis_url, redis_client):
    topic = _topic("poison")
    group = f"g-{uuid.uuid4().hex[:6]}"
    received: list[dict] = []

    async def handler(payload):
        received.append(payload)

    stream = RedisStreamsEventBus.stream_key(topic)
    await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    await redis_client.xadd(stream, {"envelope": "이것은-유효한-JSON이-아니다"})

    bus = RedisStreamsEventBus(redis_url=redis_url, consumer_group=group, block_ms=100)
    bus.subscribe(topic, handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    try:
        # 오염된 엔트리가 워커를 죽이지 않는지 — 이후 정상 이벤트가 처리되는지로 확인
        await bus.publish(topic, {"ok": True})
        await _wait_until(lambda: len(received) == 1, timeout=5.0)
        await asyncio.sleep(0.2)
        pending = await redis_client.xpending(stream, group)
        assert pending["pending"] == 0
    finally:
        await bus.stop()

    assert received == [{"ok": True}]


# ----------------------------------------------------------------------
# 실패 주입 — Redis 일시 장애에서도 워커가 죽지 않고 회복한다
# ----------------------------------------------------------------------


async def test_transient_redis_read_failure_recovers(redis_url, monkeypatch):
    topic = _topic("transient_failure")
    received: list[dict] = []

    async def handler(payload):
        received.append(payload)

    bus = RedisStreamsEventBus(redis_url=redis_url, block_ms=100)
    bus.subscribe(topic, handler, criticality=HandlerCriticality.CRITICAL)

    real_xreadgroup = bus._redis.xreadgroup
    call_state = {"count": 0}

    async def flaky_xreadgroup(*args, **kwargs):
        call_state["count"] += 1
        if call_state["count"] <= 2:
            raise ConnectionError("주입된 일시적 Redis 장애")
        return await real_xreadgroup(*args, **kwargs)

    monkeypatch.setattr(bus._redis, "xreadgroup", flaky_xreadgroup)

    await bus.start()
    try:
        await bus.publish(topic, {"n": 1})
        await _wait_until(lambda: len(received) == 1, timeout=10.0)
    finally:
        await bus.stop()

    assert call_state["count"] > 2, "장애 주입이 실제로 걸리지 않았다"
    assert received == [{"n": 1}]


# ----------------------------------------------------------------------
# 성능 단언 (ADR-2026-09-09-C Decision 1 — 전용 event_bus 행이 없어 가장
# 가까운 팬아웃 계열 예산인 "WS 팬아웃 p95 500ms"를 그대로 차용)
# ----------------------------------------------------------------------


@pytest.mark.perf
async def test_dispatch_latency_p95_under_ws_fanout_budget(redis_url):
    topic = _topic("perf")
    n = 200
    latencies: list[float] = []

    async def handler(payload):
        latencies.append(time.monotonic() - payload["sent_at"])

    bus = RedisStreamsEventBus(redis_url=redis_url, block_ms=100, batch_size=50)
    bus.subscribe(topic, handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    try:
        for _ in range(n):
            await bus.publish(topic, {"sent_at": time.monotonic()})
        await _wait_until(lambda: len(latencies) == n, timeout=30.0)
    finally:
        await bus.stop()

    ordered = sorted(latencies)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    assert p95 < 0.5, f"p95 dispatch 지연 {p95:.3f}s — WS 팬아웃 예산(500ms) 초과"
