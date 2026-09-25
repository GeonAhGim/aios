"""4.3 / 4.4 / 4.5 — InProcessEventBus.

Spec: 05_communication_architecture_v1.2.md#§5.2, §5.5, §5.6;
08_test_plan_v1.2.md#§8.6 (backpressure policy)

Deviation from audit_log integration: §5.5 requires "all handler exceptions are
automatically recorded in audit_log", but the actual audit_log recording utility
(worktree 7.4) and DB session layer do not yet exist at this point (earlier than
worktrees 4 and 7). This class is designed to accept an audit_sink callback —
once 7.4 is ready, only pass its implementation through (same principle as §5.1
of hiding the EventBus itself behind an interface). The default falls back to
standard logging for recording.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.event_bus.bus import EventBus, EventHandler
from src.core.event_bus.envelope import EventEnvelope, unwrap, wrap
from src.core.event_bus.policy import HandlerCriticality
from src.core.exceptions import EventHandlerError
from src.core.observability.context import bind

logger = logging.getLogger(__name__)

AuditSink = Callable[[dict[str, Any]], Awaitable[None]]

# §8.6 Draft policy
DEFAULT_MAX_QUEUE_DEPTH = 1000
DEFAULT_BACKPRESSURE_SUSTAINED_SECONDS = 60.0
# §5.5 Draft retry policy
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0

# Event Bus self-topic that signals to upper layers when the queue is persistently full.
# The actual switch to domain-specific topics like market.distrust.entered is the
# responsibility of the safety layer (worktree #9) that subscribes to this event —
# the generic infrastructure module Event Bus should not know domain names directly.
BACKPRESSURE_SUSTAINED_TOPIC = "event_bus.queue.backpressure_sustained"
HANDLER_ESCALATED_TOPIC = "event_bus.handler.escalated"


async def _default_audit_sink(record: dict[str, Any]) -> None:
    logger.warning("audit_log 기록 유틸(7.4) 미연동 — 임시 로깅: %s", record)


class InProcessEventBus(EventBus):
    """Phase 1 implementation. asyncio.Queue-based per-topic queues + worker coroutines.
    Operates within a single process only — multi-process/server distribution is a
    Phase 4+ expansion target."""

    def __init__(
        self,
        *,
        max_queue_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        backpressure_sustained_seconds: float = DEFAULT_BACKPRESSURE_SUSTAINED_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_initial_delay_seconds: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._max_queue_depth = max_queue_depth
        self._backpressure_sustained_seconds = backpressure_sustained_seconds
        self._max_retries = max_retries
        self._retry_initial_delay_seconds = retry_initial_delay_seconds
        self._audit_sink: AuditSink = audit_sink or _default_audit_sink

        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._subscribers: dict[str, list[tuple[EventHandler, HandlerCriticality]]] = {}
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}
        self._queue_full_since: dict[str, float] = {}
        self._running = False

    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        self._subscribers.setdefault(topic, []).append((handler, criticality))
        if self._running:
            self._ensure_worker(topic)

    async def publish(self, topic: str, payload: Any) -> None:
        """PLT-06 — packages the PLT-01 context at publish time into an envelope
        (`EventEnvelope`) and places it in the queue. The worker restores the context
        during handler execution using this envelope."""
        queue = self._get_or_create_queue(topic)
        if self._running:
            self._ensure_worker(topic)
        envelope = wrap(topic, payload)
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            await self._handle_backpressure(topic)
            return
        self._queue_full_since.pop(topic, None)

    async def start(self) -> None:
        self._running = True
        for topic in self._subscribers:
            self._ensure_worker(topic)

    async def stop(self) -> None:
        """Graceful shutdown — each worker finishes in-flight events
        (does not drain all remaining events still queued)."""
        self._running = False
        tasks = list(self._worker_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_tasks.clear()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _get_or_create_queue(self, topic: str) -> asyncio.Queue[Any]:
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue(maxsize=self._max_queue_depth)
        return self._queues[topic]

    def _ensure_worker(self, topic: str) -> None:
        if topic in self._worker_tasks and not self._worker_tasks[topic].done():
            return
        queue = self._get_or_create_queue(topic)
        self._worker_tasks[topic] = asyncio.create_task(self._worker_loop(topic, queue))

    async def _worker_loop(self, topic: str, queue: asyncio.Queue[Any]) -> None:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self._running:
                    return
                continue
            envelope, payload = unwrap(item)
            if envelope is None:  # Transitional compatibility — values enqueued without envelope
                envelope = wrap(topic, payload)
            try:
                for handler, criticality in list(self._subscribers.get(topic, [])):
                    await self._dispatch(topic, handler, criticality, envelope, payload)
            except Exception:  # noqa: BLE001 — #15 defense in depth: _dispatch already consumes handler/
                # audit_sink exceptions, but an unexpected exception must never kill this
                # worker task (from that moment on, all subsequent events for this topic
                # would silently stop being processed).
                logger.exception("[%s] 워커 루프에서 예기치 못한 예외 — 워커는 계속 동작", topic)
            finally:
                queue.task_done()
            if not self._running:
                return

    async def _dispatch(
        self,
        topic: str,
        handler: EventHandler,
        criticality: HandlerCriticality,
        envelope: EventEnvelope,
        payload: Any,
    ) -> None:
        """PLT-06 — binds trace_id/tenant_id/actor_subject_id from the envelope only
        during handler (including retry) execution. `bind()` always restores the
        previous value on context manager exit, so the context does not leak even
        if the handler raises."""
        with bind(
            trace_id=envelope.trace_id,
            tenant_id=envelope.tenant_id,
            actor_subject_id=envelope.actor_subject_id,
        ):
            try:
                await handler(payload)
            except Exception as exc:  # noqa: BLE001 — intentionally catches all handler exceptions
                if criticality == HandlerCriticality.SAFE:
                    await self._handle_safe_error(topic, handler, payload, exc)
                else:
                    await self._handle_critical_error(topic, handler, payload, exc)

    async def _handle_safe_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        """log_and_continue — continues processing without affecting other handlers."""
        wrapped = EventHandlerError(f"[{topic}] SAFE handler 실패: {exc}")
        logger.warning("%s", wrapped, exc_info=exc)
        # Red team audit (docs/RED_TEAM_FINDINGS.md #15) — even if audit_sink itself
        # fails (e.g., connection error after real DB integration), this exception
        # must not propagate to _worker_loop, which would silently kill the worker
        # task for that topic. Consumed here so audit record failure does not block
        # handler processing itself.
        try:
            await self._audit_sink(
                {
                    "actor_agent": "event_bus",
                    "action_type": "handler_error_safe",
                    "target_type": "topic",
                    "target_id": topic,
                    "decision_data": {"payload_repr": repr(payload), "error": str(exc)},
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("[%s] audit_sink 호출 실패(SAFE handler 오류 기록 중)", topic)

    async def _handle_critical_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        """escalate_and_retry — up to self._max_retries with exponential backoff.
        If all fail, escalates to HANDLER_ESCALATED_TOPIC (Circuit Breaker integration
        is the responsibility of the side subscribing to this topic, worktree #9)."""
        last_exc = exc
        for attempt in range(self._max_retries):
            delay = self._retry_initial_delay_seconds * (2**attempt)
            await asyncio.sleep(delay)
            try:
                await handler(payload)
                return  # Retry succeeded
            except Exception as retry_exc:  # noqa: BLE001
                last_exc = retry_exc

        wrapped = EventHandlerError(
            f"[{topic}] CRITICAL handler {self._max_retries}회 재시도 모두 실패: {last_exc}"
        )
        logger.error("%s", wrapped, exc_info=last_exc)
        try:
            await self._audit_sink(
                {
                    "actor_agent": "event_bus",
                    "action_type": "handler_error_critical_escalated",
                    "target_type": "topic",
                    "target_id": topic,
                    "decision_data": {
                        "payload_repr": repr(payload),
                        "error": str(last_exc),
                        "retries": self._max_retries,
                    },
                }
            )
        except Exception:  # noqa: BLE001 — same principle as #15, does not kill the worker task
            logger.exception(
                "[%s] audit_sink 호출 실패(CRITICAL handler 에스컬레이션 기록 중)", topic
            )
        try:
            await self.publish(
                HANDLER_ESCALATED_TOPIC,
                {"topic": topic, "error": str(last_exc), "retries": self._max_retries},
            )
        except Exception:  # noqa: BLE001 — failure of escalation itself does not block the original flow
            logger.exception("HANDLER_ESCALATED_TOPIC 발행 실패")

    async def _handle_backpressure(self, topic: str) -> None:
        """§8.6 — Reject new publish + WARNING log. Does not drop oldest."""
        logger.warning("Event Bus 큐 포화로 publish 거부: topic=%s", topic)
        now = time.monotonic()
        full_since = self._queue_full_since.setdefault(topic, now)
        # Excluding the meta-topic itself from recursive escalation prevents infinite recursion.
        sustained = now - full_since >= self._backpressure_sustained_seconds
        if topic != BACKPRESSURE_SUSTAINED_TOPIC and sustained:
            try:
                await self.publish(
                    BACKPRESSURE_SUSTAINED_TOPIC,
                    {"topic": topic, "sustained_seconds": now - full_since},
                )
            except Exception:  # noqa: BLE001
                logger.exception("BACKPRESSURE_SUSTAINED_TOPIC 발행 실패")
