"""17.2 — 알림 유형별 채널 정책.

Spec: 기능설계문서_v1.20.md#FD-17.2

변경 빈도가 낮으므로 코드 상수로 시작(Draft) — 운영 중 빈번히 바뀌면 DB
테이블로 이전한다(FD-17.2 원문 명시).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class ChannelRule(BaseModel):
    channel: NotificationChannel
    user_overridable: bool  # False면 이 채널은 사용자가 끌 수 없다(4.9 강제원칙)


class ChannelPolicy(BaseModel):
    rules: list[ChannelRule] = Field(default_factory=list)

    @property
    def forced_channels(self) -> list[NotificationChannel]:
        return [r.channel for r in self.rules if not r.user_overridable]


# FD-17.2 표 그대로. 화이트리스트 방식 — 새 이벤트는 기본적으로 강제 채널이
# 아니다(강제 채널로 실수 편입되는 것을 방지, 반대 방향은 안전).
_POLICY_TABLE: dict[str, ChannelPolicy] = {
    "approval.request.created": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
        ]
    ),
    "watchdog.decision.triggered": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
        ]
    ),
    "risk.circuit_breaker.reactivation_requested": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
        ]
    ),
    "security.withdrawal_whitelist.added": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
        ]
    ),
    "execution.safety_block.applied": ChannelPolicy(
        rules=[ChannelRule(channel=NotificationChannel.IN_APP, user_overridable=False)]
    ),
    "risk_profile.match.warned": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.IN_APP, user_overridable=False),
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True),
        ]
    ),
    "marketplace.purchase.requested": ChannelPolicy(
        rules=[ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True)]
    ),
    "marketplace.payment.confirmed": ChannelPolicy(
        rules=[ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True)]
    ),
    "strategy.verification.completed": ChannelPolicy(
        rules=[ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True)]
    ),
}

_DEFAULT_POLICY = ChannelPolicy(
    rules=[ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True)]
)


def get_channel_policy(event_type: str) -> ChannelPolicy:
    """예외 상황(FD-17.2) — 표에 없는 신규 이벤트는 fail-safe 기본값
    (이메일만, 사용자가 끌 수 있음)."""
    return _POLICY_TABLE.get(event_type, _DEFAULT_POLICY)
