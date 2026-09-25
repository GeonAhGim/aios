"""4.1 — Event Bus abstract interface.

Spec: 05_communication_architecture_v1.2.md#§5.2

Phase 1 provides only a single-process in-memory implementation (InProcessEventBus),
but hiding it behind this interface allows swapping in a RedisEventBus later
without modifying subscriber code (§5.1 principle).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.event_bus.policy import HandlerCriticality

EventHandler = Callable[[Any], Awaitable[None]]


class EventBus(ABC):
    """4.1 ⑫ Internal implementation of Event Bus for the Trading Core."""

    @abstractmethod
    async def publish(self, topic: str, payload: Any) -> None: ...

    @abstractmethod
    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        """criticality must be specified every time without a default (§5.5) —
        structurally preventing accidental registration of state-change logic as SAFE."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown — wait for in-flight events to complete before stopping."""
        ...
