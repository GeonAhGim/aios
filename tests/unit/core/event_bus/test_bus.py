"""Coverage for src/core/event_bus/bus.py — the EventBus abstract interface.

Spec: 05_communication_architecture_v1.2.md#§5.2, §5.5
"""

from __future__ import annotations

import pytest

from src.core.event_bus.bus import EventBus, EventHandler
from src.core.event_bus.policy import HandlerCriticality


class _SuperCallingBus(EventBus):
    """Delegates to the abstract bodies via super() so bus.py's own statements
    (the `...` placeholders) execute and register as covered, not just overridden."""

    def __init__(self) -> None:
        self.subscribed: list[tuple[str, EventHandler, HandlerCriticality]] = []

    async def publish(self, topic: str, payload: object) -> None:
        return await super().publish(topic, payload)

    def subscribe(
        self, topic: str, handler: EventHandler, *, criticality: HandlerCriticality
    ) -> None:
        self.subscribed.append((topic, handler, criticality))
        return super().subscribe(topic, handler, criticality=criticality)

    async def start(self) -> None:
        return await super().start()

    async def stop(self) -> None:
        return await super().stop()


class _IncompleteBus(EventBus):
    """Only implements publish — used to assert ABC enforcement (negative test)."""

    async def publish(self, topic: str, payload: object) -> None:
        return None


async def _noop_handler(payload: object) -> None:
    return None


@pytest.mark.asyncio
async def test_super_calling_subclass_executes_abstract_bodies() -> None:
    bus = _SuperCallingBus()
    assert await bus.publish("t", {"x": 1}) is None
    bus.subscribe("t", _noop_handler, criticality=HandlerCriticality.SAFE)
    assert bus.subscribed == [("t", _noop_handler, HandlerCriticality.SAFE)]
    assert await bus.start() is None
    assert await bus.stop() is None


def test_event_bus_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        EventBus()  # type: ignore[abstract]


def test_incomplete_subclass_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        _IncompleteBus()  # type: ignore[abstract]


def test_subscribe_requires_criticality_keyword() -> None:
    bus = _SuperCallingBus()
    with pytest.raises(TypeError):
        bus.subscribe("t", _noop_handler, HandlerCriticality.SAFE)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_publish_failure_propagates_from_override(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _SuperCallingBus()

    async def _boom(self: _SuperCallingBus, topic: str, payload: object) -> None:
        raise RuntimeError("dependency unavailable")

    monkeypatch.setattr(_SuperCallingBus, "publish", _boom)

    with pytest.raises(RuntimeError, match="dependency unavailable"):
        await bus.publish("t", {})
