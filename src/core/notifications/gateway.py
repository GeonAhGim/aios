"""17.1 — Notification dispatch gateway.

Spec: 기능설계문서_v1.20.md#FD-17.1

Design principle (FD-17 original) — Do not create new infrastructure. Attach
as a Subscriber to EventBus (#05) and register with CRITICAL criticality — on
send failure, reuse EventBus's exponential backoff retry (up to 5 attempts) +
final-failure audit_log recording (§4.5) as-is (just raise EventHandlerError;
this gateway does not implement retry logic directly).

Deviation: Actual email/push senders (SMTP · FCM/APNs) are still undetermined
(Draft, FD-17.1 original), so they are injected as callbacks. db/session.py
(worktree #16) does not exist yet, so notification table records receive the
asyncpg pool directly (same pattern as audit_log.py).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from src.core.event_bus.bus import EventBus
from src.core.event_bus.policy import HandlerCriticality
from src.core.exceptions import EventHandlerError
from src.core.notifications.channel_policy import NotificationChannel, get_channel_policy

# (user_id, event_type, payload) -> whether channel send succeeded
SendChannelFn = Callable[[UUID, str, dict[str, Any]], Awaitable[bool]]

# All topic types listed by FD-17.1 — EventBus subscription targets.
SUBSCRIBED_EVENT_TYPES = (
    "approval.request.created",
    "watchdog.decision.triggered",
    "risk.circuit_breaker.reactivation_requested",
    "security.withdrawal_whitelist.added",
    "execution.safety_block.applied",
    "risk_profile.match.warned",
    "marketplace.purchase.requested",
    "marketplace.payment.confirmed",
    "strategy.verification.completed",
    "alert.triggered",
)


def _coerce_user_id(raw: Any) -> UUID | None:
    if raw is None or isinstance(raw, UUID):
        return raw
    return UUID(raw)


class NotificationGateway:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        senders: dict[NotificationChannel, SendChannelFn] | None = None,
    ) -> None:
        self._pool = pool
        # Undetermined channels (Draft) are treated as "send failure" by default —
        # do not silently pretend success (FD-17.1 principle: "inability to confirm
        # delivery is itself a safety issue").
        self._senders = senders or {}

    def register(self, event_bus: EventBus) -> None:
        for event_type in SUBSCRIBED_EVENT_TYPES:
            event_bus.subscribe(
                event_type, self.handle_event, criticality=HandlerCriticality.CRITICAL
            )

    async def handle_event(self, payload: dict[str, Any]) -> None:
        event_type = payload["event_type"]
        user_id = _coerce_user_id(payload.get("user_id"))
        if user_id is None:
            # All FD-17 events target a specific user — an event published without
            # user_id is a bug in the publisher, so do not silently ignore it.
            raise EventHandlerError(f"[{event_type}] user_id 없는 알림 이벤트")
        policy = get_channel_policy(event_type)

        failures: list[NotificationChannel] = []
        for rule in policy.rules:
            sender = self._senders.get(rule.channel)
            success = await sender(user_id, event_type, payload) if sender is not None else False
            await self._record(user_id, event_type, rule.channel, success)
            if not success:
                failures.append(rule.channel)

        if failures:
            # Triggers EventBus's CRITICAL retry (up to 5)+final-failure audit_log
            # recording as-is — this gateway does not implement retry directly.
            raise EventHandlerError(
                f"[{event_type}] 알림 발송 실패 채널: {[c.value for c in failures]}"
            )

    async def _record(
        self, user_id: UUID | None, event_type: str, channel: NotificationChannel, success: bool
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notifications (user_id, event_type, channel, status, payload_summary)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                user_id,
                event_type,
                channel.value,
                "SENT" if success else "FAILED",
                json.dumps({}),
            )
