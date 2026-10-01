"""Coverage for src/core/event_bus/in_process.py — InProcessEventBus.

Spec: 05_communication_architecture_v1.2.md#§5.2, §5.5, §5.6;
08_test_plan_v1.2.md#§8.6 (backpressure policy)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.event_bus.envelope import EventEnvelope, wrap
from src.core.event_bus.in_process import (
    BACKPRESSURE_SUSTAINED_TOPIC,
    HANDLER_ESCALATED_TOPIC,
    InProcessEventBus,
)
from src.core.event_bus.policy import HandlerCriticality


async def _collect_within(queue: asyncio.Queue[Any], n: int, timeout: float = 2.0) -> list[Any]:
    async def _drain() -> list[Any]:
        items: list[Any] = []
        for _ in range(n):
            items.append(await queue.get())
        return items

    return await asyncio.wait_for(_drain(), timeout=timeout)


@pytest.mark.asyncio
async def test_publish_then_subscribe_safe_handler_receives_payload() -> None:
    bus = InProcessEventBus()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("topic.a", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.a", {"x": 1})

    items = await _collect_within(received, 1)
    assert items == [{"x": 1}]
    await bus.stop()


@pytest.mark.asyncio
async def test_subscribe_after_start_still_gets_worker() -> None:
    bus = InProcessEventBus()
    await bus.start()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("topic.late", handler, criticality=HandlerCriticality.SAFE)
    await bus.publish("topic.late", "hello")

    items = await _collect_within(received, 1)
    assert items == ["hello"]
    await bus.stop()


@pytest.mark.asyncio
async def test_envelope_less_item_is_wrapped_transitionally() -> None:
    bus = InProcessEventBus()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("topic.raw", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    queue = bus._get_or_create_queue("topic.raw")
    await queue.put({"raw": True})

    items = await _collect_within(received, 1)
    assert items == [{"raw": True}]
    await bus.stop()


@pytest.mark.asyncio
async def test_safe_handler_failure_logs_and_calls_audit_sink_without_raising() -> None:
    audit_calls: list[dict[str, Any]] = []

    async def audit_sink(record: dict[str, Any]) -> None:
        audit_calls.append(record)

    bus = InProcessEventBus(audit_sink=audit_sink)

    async def failing_handler(payload: Any) -> None:
        raise ValueError("boom")

    bus.subscribe("topic.safe_fail", failing_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.safe_fail", {"y": 2})

    async def _wait() -> None:
        while not audit_calls:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=2.0)

    assert audit_calls[0]["action_type"] == "handler_error_safe"
    assert audit_calls[0]["target_id"] == "topic.safe_fail"
    await bus.stop()


@pytest.mark.asyncio
async def test_safe_handler_audit_sink_failure_does_not_kill_worker() -> None:
    async def failing_audit_sink(record: dict[str, Any]) -> None:
        raise RuntimeError("audit sink unavailable")

    bus = InProcessEventBus(audit_sink=failing_audit_sink)
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        if payload == "fail":
            raise ValueError("boom")
        await received.put(payload)

    bus.subscribe("topic.audit_fail", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.audit_fail", "fail")
    await bus.publish("topic.audit_fail", "ok")

    items = await _collect_within(received, 1)
    assert items == ["ok"]
    await bus.stop()


@pytest.mark.asyncio
async def test_critical_handler_retries_then_succeeds() -> None:
    bus = InProcessEventBus(max_retries=3, retry_initial_delay_seconds=0.001)
    attempts = {"count": 0}
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def flaky_handler(payload: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise ValueError("transient")
        await received.put(payload)

    bus.subscribe("topic.retry_ok", flaky_handler, criticality=HandlerCriticality.CRITICAL)
    await bus.start()
    await bus.publish("topic.retry_ok", "payload")

    items = await _collect_within(received, 1)
    assert items == ["payload"]
    assert attempts["count"] == 2
    await bus.stop()


@pytest.mark.asyncio
async def test_critical_handler_exhausts_retries_and_escalates() -> None:
    bus = InProcessEventBus(max_retries=2, retry_initial_delay_seconds=0.001)
    audit_calls: list[dict[str, Any]] = []

    async def audit_sink(record: dict[str, Any]) -> None:
        audit_calls.append(record)

    bus._audit_sink = audit_sink  # type: ignore[assignment]

    escalated: asyncio.Queue[Any] = asyncio.Queue()

    async def escalation_handler(payload: Any) -> None:
        await escalated.put(payload)

    async def always_failing_handler(payload: Any) -> None:
        raise ValueError("permanent failure")

    bus.subscribe(
        "topic.retry_exhaust", always_failing_handler, criticality=HandlerCriticality.CRITICAL
    )
    bus.subscribe(HANDLER_ESCALATED_TOPIC, escalation_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.retry_exhaust", "payload")

    items = await _collect_within(escalated, 1, timeout=3.0)
    assert items[0]["topic"] == "topic.retry_exhaust"
    assert items[0]["retries"] == 2
    assert any(c["action_type"] == "handler_error_critical_escalated" for c in audit_calls)
    await bus.stop()


@pytest.mark.asyncio
async def test_critical_handler_escalation_publish_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = InProcessEventBus(max_retries=1, retry_initial_delay_seconds=0.001)

    async def always_failing_handler(payload: Any) -> None:
        raise ValueError("permanent failure")

    bus.subscribe(
        "topic.escalate_fail", always_failing_handler, criticality=HandlerCriticality.CRITICAL
    )

    original_publish = bus.publish

    async def _boom_on_escalation(topic: str, payload: Any) -> None:
        if topic == HANDLER_ESCALATED_TOPIC:
            raise RuntimeError("escalation publish broken")
        await original_publish(topic, payload)

    monkeypatch.setattr(bus, "publish", _boom_on_escalation)
    await bus.start()
    await bus._dispatch(
        "topic.escalate_fail",
        always_failing_handler,
        HandlerCriticality.CRITICAL,
        wrap("topic.escalate_fail", "x"),
        "x",
    )
    await bus.stop()


@pytest.mark.asyncio
async def test_publish_rejected_when_queue_full() -> None:
    bus = InProcessEventBus(max_queue_depth=1)
    queue = bus._get_or_create_queue("topic.full")
    queue.put_nowait(wrap("topic.full", "already-there"))

    await bus.publish("topic.full", "rejected")

    assert queue.qsize() == 1
    assert "topic.full" in bus._queue_full_since


@pytest.mark.asyncio
async def test_sustained_backpressure_escalates_to_backpressure_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = InProcessEventBus(max_queue_depth=1, backpressure_sustained_seconds=0.0)
    queue = bus._get_or_create_queue("topic.sustained")
    queue.put_nowait(wrap("topic.sustained", "already-there"))

    escalated: asyncio.Queue[Any] = asyncio.Queue()

    async def escalation_handler(payload: Any) -> None:
        await escalated.put(payload)

    bus.subscribe(
        BACKPRESSURE_SUSTAINED_TOPIC, escalation_handler, criticality=HandlerCriticality.SAFE
    )
    await bus.start()
    await bus.publish("topic.sustained", "rejected")

    items = await _collect_within(escalated, 1)
    assert items[0]["topic"] == "topic.sustained"
    await bus.stop()


@pytest.mark.asyncio
async def test_backpressure_topic_itself_does_not_recurse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = InProcessEventBus(max_queue_depth=1, backpressure_sustained_seconds=0.0)
    queue = bus._get_or_create_queue(BACKPRESSURE_SUSTAINED_TOPIC)
    queue.put_nowait(wrap(BACKPRESSURE_SUSTAINED_TOPIC, "already-there"))

    await bus._handle_backpressure(BACKPRESSURE_SUSTAINED_TOPIC)

    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_backpressure_escalation_publish_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = InProcessEventBus(max_queue_depth=1, backpressure_sustained_seconds=0.0)
    queue = bus._get_or_create_queue("topic.sustained_boom")
    queue.put_nowait(wrap("topic.sustained_boom", "already-there"))

    async def _boom(topic: str, payload: Any) -> None:
        raise RuntimeError("publish broken")

    monkeypatch.setattr(bus, "publish", _boom)

    await bus._handle_backpressure("topic.sustained_boom")


@pytest.mark.asyncio
async def test_worker_loop_survives_unexpected_dispatch_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = InProcessEventBus()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("topic.worker_resilient", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    original_dispatch = bus._dispatch
    call_count = {"n": 0}

    async def _dispatch_boom_once(
        topic: str,
        h: Any,
        criticality: HandlerCriticality,
        envelope: EventEnvelope,
        payload: Any,
    ) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("unexpected dispatch failure")
        await original_dispatch(topic, h, criticality, envelope, payload)

    monkeypatch.setattr(bus, "_dispatch", _dispatch_boom_once)

    await bus.publish("topic.worker_resilient", "first")
    await bus.publish("topic.worker_resilient", "second")

    items = await _collect_within(received, 1, timeout=3.0)
    assert items == ["second"]
    await bus.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop() -> None:
    bus = InProcessEventBus()
    await bus.stop()
    assert bus._worker_tasks == {}


@pytest.mark.asyncio
async def test_default_audit_sink_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    bus = InProcessEventBus()

    async def failing_handler(payload: Any) -> None:
        raise ValueError("boom")

    bus.subscribe("topic.default_audit", failing_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()

    async def _wait() -> None:
        while "audit_log" not in caplog.text:
            await asyncio.sleep(0.01)

    with caplog.at_level("WARNING"):
        await bus.publish("topic.default_audit", "x")
        await asyncio.wait_for(_wait(), timeout=2.0)
    await bus.stop()


@pytest.mark.asyncio
async def test_dispatch_binds_envelope_context_during_handler_execution() -> None:
    bus = InProcessEventBus()
    seen_trace_ids: list[Any] = []

    from src.core.observability.context import current

    async def handler(payload: Any) -> None:
        seen_trace_ids.append(current().trace_id)

    envelope = wrap("topic.ctx", {"z": 1})
    await bus._dispatch("topic.ctx", handler, HandlerCriticality.SAFE, envelope, envelope.payload)

    assert seen_trace_ids == [envelope.trace_id]


@pytest.mark.asyncio
async def test_publish_without_subscribers_does_not_error() -> None:
    bus = InProcessEventBus()
    await bus.start()
    await bus.publish("topic.nobody_listens", {"a": 1})
    await asyncio.sleep(0.05)
    await bus.stop()


@pytest.mark.asyncio
async def test_negative_empty_topic_string_is_rejected_by_no_special_casing() -> None:
    """Negative test — an empty-string topic is not special-cased; it behaves like any
    other topic name (queue/worker get created under the empty-string key)."""
    bus = InProcessEventBus()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("", "x")

    items = await _collect_within(received, 1)
    assert items == ["x"]
    await bus.stop()


@pytest.mark.asyncio
async def test_negative_none_payload_is_delivered_as_is() -> None:
    bus = InProcessEventBus()
    received: asyncio.Queue[Any] = asyncio.Queue()

    async def handler(payload: Any) -> None:
        await received.put(payload)

    bus.subscribe("topic.none_payload", handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.none_payload", None)

    items = await _collect_within(received, 1)
    assert items == [None]
    await bus.stop()


@pytest.mark.asyncio
async def test_negative_subscribe_without_criticality_keyword_raises() -> None:
    """Negative test — mirrors the EventBus ABC contract: criticality must be
    keyword-only, structurally preventing accidental SAFE-by-default registration."""
    bus = InProcessEventBus()

    async def handler(payload: Any) -> None:
        return None

    with pytest.raises(TypeError):
        bus.subscribe("topic.kw", handler, HandlerCriticality.SAFE)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_multiple_subscribers_on_same_topic_all_receive_payload() -> None:
    bus = InProcessEventBus()
    received_a: asyncio.Queue[Any] = asyncio.Queue()
    received_b: asyncio.Queue[Any] = asyncio.Queue()

    async def handler_a(payload: Any) -> None:
        await received_a.put(payload)

    async def handler_b(payload: Any) -> None:
        await received_b.put(payload)

    bus.subscribe("topic.multi", handler_a, criticality=HandlerCriticality.SAFE)
    bus.subscribe("topic.multi", handler_b, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic.multi", "shared")

    assert (await _collect_within(received_a, 1))[0] == "shared"
    assert (await _collect_within(received_b, 1))[0] == "shared"
    await bus.stop()
