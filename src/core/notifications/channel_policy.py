"""17.2 — Per-notification-type channel policy.

Spec: 기능설계문서_v1.20.md#FD-17.2

Low change frequency — start as code constants (Draft); migrate to a DB
table if operations requires frequent runtime changes (per FD-17.2 original).
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
    user_overridable: bool  # False = user cannot disable (Section 4.9 forced-principle)


class ChannelPolicy(BaseModel):
    rules: list[ChannelRule] = Field(default_factory=list)

    @property
    def forced_channels(self) -> list[NotificationChannel]:
        return [r.channel for r in self.rules if not r.user_overridable]


# Per FD-17.2 table. Whitelist approach — new events are NOT forced channels by
# default (prevents accidental forced inclusion; the opposite direction is safe).
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
    "alert.triggered": ChannelPolicy(
        rules=[
            ChannelRule(channel=NotificationChannel.IN_APP, user_overridable=False),
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True),
        ]
    ),
}

_DEFAULT_POLICY = ChannelPolicy(
    rules=[ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True)]
)


def get_channel_policy(event_type: str) -> ChannelPolicy:
    """Fail-safe default for unlisted event types (FD-17.2): email only,
    user-overridable."""
    return _POLICY_TABLE.get(event_type, _DEFAULT_POLICY)
