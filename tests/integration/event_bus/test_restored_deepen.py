"""task-6704 고아 산출물 회수 DEEPEN — negative/failure-injection 보강.

`InProcessEventBus`(src/core/event_bus/in_process.py)의 fail-closed 경로를
Redis 없이(기본 CI 스위트 포함) 검증한다. 관련 red-gate 재현은
`test_in_process_baseline_red_gate.py`, Redis 백엔드 쪽 실패 주입은
`test_redis_streams_event_bus.py`(`pytest -m redis`)를 참고.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.event_bus.envelope import EventEnvelope, unwrap
from src.core.event_bus.in_process import HANDLER_ESCALATED_TOPIC, InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality


async def test_backpressure_rejects_publish_without_raising() -> None:
    """Negative: a full queue must reject the new event, not raise or block the caller.

    §8.6 draft policy — fail-closed is "reject + WARNING log", never an unhandled
    exception propagating out of publish().
    """
    bus = InProcessEventBus(max_queue_depth=1)
    # Workers are never started (start() not called), so nothing drains the queue —
    # this deterministically forces the second publish() into the backpressure path.
    await bus.publish("topic", {"seq": 0})
    await bus.publish("topic", {"seq": 1})  # must not raise asyncio.QueueFull

    queue = bus._get_or_create_queue("topic")
    assert queue.qsize() == 1  # the rejected second event was never enqueued


async def test_safe_handler_failure_does_not_stop_other_subscribers() -> None:
    """Negative: a SAFE handler exception must not block sibling handlers on the
    same topic (log_and_continue, policy.py EventBusPolicy.ON_HANDLER_ERROR)."""
    received: list[str] = []

    async def failing_handler(payload: dict) -> None:
        raise RuntimeError("boom")

    async def healthy_handler(payload: dict) -> None:
        received.append(payload["seq"])

    bus = InProcessEventBus()
    bus.subscribe("topic", failing_handler, criticality=HandlerCriticality.SAFE)
    bus.subscribe("topic", healthy_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic", {"seq": "only"})
    await asyncio.sleep(0.2)
    await bus.stop()

    assert received == ["only"]


async def test_critical_handler_exhausts_retries_and_escalates() -> None:
    """Negative: a CRITICAL handler that always fails must exhaust its configured
    retry budget and then publish to HANDLER_ESCALATED_TOPIC — it must never be
    silently dropped (escalate_and_retry, policy.py)."""
    escalations: list[dict] = []

    async def always_fails(payload: dict) -> None:
        raise ValueError("handler is broken")

    async def capture_escalation(payload: dict) -> None:
        escalations.append(payload)

    bus = InProcessEventBus(max_retries=1, retry_initial_delay_seconds=0.001)
    bus.subscribe("topic", always_fails, criticality=HandlerCriticality.CRITICAL)
    bus.subscribe(HANDLER_ESCALATED_TOPIC, capture_escalation, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic", {"seq": "x"})
    await asyncio.sleep(0.3)
    await bus.stop()

    assert len(escalations) == 1
    assert escalations[0]["topic"] == "topic"
    assert escalations[0]["retries"] == 1


async def test_unwrap_rejects_non_envelope_without_raising() -> None:
    """Negative: `unwrap()` on a value that violates the EventEnvelope contract
    (not a pydantic EventEnvelope instance) must degrade to `(None, obj)`
    rather than raise — this is the explicit migration-compatibility contract
    documented in envelope.py, not an accidental permissive fallback."""
    envelope, payload = unwrap({"not": "an envelope"})
    assert envelope is None
    assert payload == {"not": "an envelope"}

    real_envelope, real_payload = unwrap(
        EventEnvelope.model_construct(
            event_id=__import__("uuid").uuid4(),
            topic="t",
            trace_id=__import__("uuid").uuid4(),
            tenant_id=None,
            actor_subject_id="system",
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            payload={"ok": True},
        )
    )
    assert isinstance(real_envelope, EventEnvelope)
    assert real_payload == {"ok": True}


async def test_audit_sink_failure_does_not_kill_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: if the audit_sink dependency itself raises (e.g. a DB
    connection error once 7.4 lands), the worker task must survive and keep
    processing subsequent events on the same topic (red team finding #15, see
    in_process.py _handle_safe_error docstring)."""
    processed: list[str] = []

    async def broken_audit_sink(record: dict) -> None:
        raise ConnectionError("audit sink unavailable")

    async def failing_handler(payload: dict) -> None:
        raise RuntimeError("boom")

    bus = InProcessEventBus(audit_sink=broken_audit_sink)
    bus.subscribe("topic", failing_handler, criticality=HandlerCriticality.SAFE)
    await bus.start()
    await bus.publish("topic", {"seq": 1})
    await asyncio.sleep(0.1)
    # If the worker task died after the first failure, this second event would
    # never be observed as "the task is still alive" — assert via task state.
    await bus.publish("topic", {"seq": 2})
    await asyncio.sleep(0.1)
    worker_task = bus._worker_tasks["topic"]
    assert not worker_task.done()
    await bus.stop()
    assert (
        processed == []
    )  # handler always fails; the invariant under test is survival, not success
