"""4.1 — Event Bus 추상 인터페이스.

Spec: 05_communication_architecture_v1.2.md#§5.2

Phase 1은 단일 프로세스 in-memory 구현(InProcessEventBus)만 제공하지만,
이 인터페이스 뒤에 숨겨두면 향후 RedisEventBus 등으로 교체해도 구독자
코드는 수정하지 않아도 된다(§5.1 원칙).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.event_bus.policy import HandlerCriticality

EventHandler = Callable[[Any], Awaitable[None]]


class EventBus(ABC):
    """4.1 ⑫ Event Bus의 Trading Core 내부 구현."""

    @abstractmethod
    async def publish(self, topic: str, payload: Any) -> None: ...

    @abstractmethod
    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        """criticality는 기본값 없이 매번 명시해야 한다(§5.5) — 상태 변경 로직을
        실수로 SAFE로 등록하는 것을 구조적으로 막기 위함."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown — 처리 중인 이벤트 완료 대기 후 종료."""
        ...
