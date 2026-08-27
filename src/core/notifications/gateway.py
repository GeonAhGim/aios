"""17.1 — 알림 발송 게이트웨이.

Spec: 기능설계문서_v1.20.md#FD-17.1

설계 원칙(FD-17 원문) — 새 인프라를 만들지 않는다. EventBus(05번)의
구독자(Subscriber)로 붙어 CRITICAL 등록한다 — 발송 실패 시 EventBus의
지수 백오프 재시도(최대 5회) + 최종 실패 시 audit_log 기록(§4.5)을 그대로
재사용한다(EventHandlerError를 던지기만 하면 됨, 이 게이트웨이가 재시도
로직을 직접 구현하지 않는다).

편차: 실제 이메일/푸시 발송기(SMTP·FCM/APNs)는 아직 미확정(Draft, FD-17.1
원문)이라 콜백으로 주입받는다. db/session.py(작업트리 16번)가 아직 없어
notifications 테이블 기록은 asyncpg pool을 직접 받는다(audit_log.py와
동일 패턴).
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

# (user_id, event_type, payload) -> 발송 성공 여부
SendChannelFn = Callable[[UUID, str, dict[str, Any]], Awaitable[bool]]

# FD-17.1이 나열한 대상 topic 전체 — EventBus 구독 대상.
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
        # 미확정 채널(Draft)은 기본적으로 "발송 실패"로 취급 — 조용히 성공한
        # 척 하지 않는다(FD-17.1 "발송됐는지 확인 못 하는 상태 자체가
        # 안전 이슈" 원칙).
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
            # FD-17의 모든 이벤트는 특정 사용자 대상 알림이다 — user_id 없이
            # 발행된 이벤트는 발행부의 버그이므로 조용히 넘기지 않는다.
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
            # EventBus의 CRITICAL 재시도(최대 5회)+최종실패 audit_log 기록을
            # 그대로 트리거한다 — 이 게이트웨이는 재시도를 직접 구현하지 않는다.
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
