"""4.2 — HandlerCriticality, EventBusPolicy.

Spec: 05_communication_architecture_v1.2.md#§5.5
"""
from __future__ import annotations

from enum import Enum


class HandlerCriticality(str, Enum):
    """Treating all handlers with log_and_continue masks failures in state-changing
    handlers (e.g. position updates), allowing internal state drift to go unnoticed
    in real time. Forces callers to declare this value at handler registration."""

    SAFE = "SAFE"  # failure has no impact on system state (e.g. logging subscribers)
    CRITICAL = "CRITICAL"  # failure may cause state inconsistency (e.g. position/ledger updates)


class EventBusPolicy:
    ON_HANDLER_ERROR = {
        HandlerCriticality.SAFE: "log_and_continue",
        HandlerCriticality.CRITICAL: "escalate_and_retry",
    }
