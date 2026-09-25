"""4.7 (M2-8 Phase2) — SAFE/CRITICAL handler dispatch policy for RedisStreamsEventBus.

Split out of `redis_streams.py` to stay under the architecture guard's
300-line-per-file cap (policy/immutable.md P6). This module owns exactly
one concern: given a parsed `EventEnvelope` and the topic's subscribers,
run each handler under the §5.5 criticality policy (log_and_continue for
SAFE, escalate_and_retry for CRITICAL) — the same policy `in_process.py`
implements, kept separate here rather than shared so neither backend's
tests are put at risk by editing the other's module.

`escalate` is injected rather than calling `self.publish` directly, so this
module has no dependency on `RedisStreamsEventBus` itself (it only needs a
way to publish the CRITICAL-exhausted event back onto the bus).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.event_bus.bus import EventHandler
from src.core.event_bus.envelope import EventEnvelope
from src.core.event_bus.policy import HandlerCriticality
from src.core.exceptions import EventHandlerError
from src.core.observability.context import bind

logger = logging.getLogger(__name__)

AuditSink = Callable[[dict[str, Any]], Awaitable[None]]
Escalate = Callable[[str, Any], Awaitable[None]]

DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0

HANDLER_ESCALATED_TOPIC = "event_bus.handler.escalated"


async def default_audit_sink(record: dict[str, Any]) -> None:
    logger.warning("audit_log sink (7.4) not wired yet — logging instead: %s", record)


class HandlerDispatcher:
    def __init__(
        self,
        *,
        escalate: Escalate,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_initial_delay_seconds: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._escalate = escalate
        self._max_retries = max_retries
        self._retry_initial_delay_seconds = retry_initial_delay_seconds
        self._audit_sink: AuditSink = audit_sink or default_audit_sink

    async def dispatch(
        self,
        topic: str,
        handler: EventHandler,
        criticality: HandlerCriticality,
        envelope: EventEnvelope,
    ) -> None:
        with bind(
            trace_id=envelope.trace_id,
            tenant_id=envelope.tenant_id,
            actor_subject_id=envelope.actor_subject_id,
        ):
            try:
                await handler(envelope.payload)
            except Exception as exc:  # noqa: BLE001 — intentionally catches every handler error
                if criticality == HandlerCriticality.SAFE:
                    await self._handle_safe_error(topic, handler, envelope.payload, exc)
                else:
                    await self._handle_critical_error(topic, handler, envelope.payload, exc)

    async def _handle_safe_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        wrapped = EventHandlerError(f"[{topic}] SAFE handler failed: {exc}")
        logger.warning("%s", wrapped, exc_info=exc)
        try:
            await self._audit_sink(
                {
                    "actor_agent": "event_bus",
                    "action_type": "handler_error_safe",
                    "target_type": "topic",
                    "target_id": topic,
                    "decision_data": {
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                        "payload_repr": repr(payload),
                        "error": str(exc),
                    },
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[%s] audit_sink call failed while recording SAFE handler error", topic
            )

    async def _handle_critical_error(
        self, topic: str, handler: EventHandler, payload: Any, exc: Exception
    ) -> None:
        last_exc = exc
        for attempt in range(self._max_retries):
            delay = self._retry_initial_delay_seconds * (2**attempt)
            await asyncio.sleep(delay)
            try:
                await handler(payload)
                return
            except Exception as retry_exc:  # noqa: BLE001
                last_exc = retry_exc

        wrapped = EventHandlerError(
            f"[{topic}] CRITICAL handler exhausted all {self._max_retries} retries: {last_exc}"
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
        except Exception:  # noqa: BLE001
            logger.exception(
                "[%s] audit_sink call failed while recording CRITICAL handler escalation", topic
            )
        try:
            await self._escalate(
                HANDLER_ESCALATED_TOPIC,
                {"topic": topic, "error": str(last_exc), "retries": self._max_retries},
            )
        except Exception:  # noqa: BLE001 — a failure to escalate must not block the original flow
            logger.exception("failed to publish HANDLER_ESCALATED_TOPIC")
