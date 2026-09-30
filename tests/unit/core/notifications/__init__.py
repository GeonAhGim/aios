"""DEEPEN(task-9987): negative/failure-injection coverage for src/core/notifications.

pytest collects this module when invoked with an explicit path
(`pytest tests/unit/core/notifications/__init__.py`) even though it is not
matched by the default `test_*.py` discovery glob — see task-9982 (sibling
DEEPEN, tests/unit/core/loader/__init__.py) for the same pattern.

Scope: src/core/notifications/gateway.NotificationGateway.handle_event
(FD-17.1) — the event_bus-facing safety gate. FD-17.1 principle: "inability
to confirm delivery is itself a safety issue", so a missing user_id, an
unroutable/malformed event, or a channel-sender failure must never be
silently swallowed (fail-closed per CLAUDE.md §3).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.core.exceptions import EventHandlerError
from src.core.notifications.channel_policy import NotificationChannel
from src.core.notifications.gateway import NotificationGateway


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, ...]] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append(args)


class _FakeAcquireCtx:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConnection()

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self.conn)


async def test_handle_event_missing_user_id_raises_event_handler_error() -> None:
    gateway = NotificationGateway(pool=_FakePool())

    with pytest.raises(EventHandlerError, match="user_id"):
        await gateway.handle_event({"event_type": "alert.triggered"})


async def test_handle_event_missing_event_type_raises_key_error() -> None:
    gateway = NotificationGateway(pool=_FakePool())

    with pytest.raises(KeyError):
        await gateway.handle_event({"user_id": str(uuid4())})


async def test_handle_event_unregistered_channel_is_treated_as_failure() -> None:
    """No sender registered for the policy's channel -> the gateway must not
    silently pretend success (FD-17.1)."""
    pool = _FakePool()
    gateway = NotificationGateway(pool=pool)

    with pytest.raises(EventHandlerError, match="알림 발송 실패"):
        await gateway.handle_event(
            {"event_type": "marketplace.payment.confirmed", "user_id": str(uuid4())}
        )
    assert pool.conn.executed  # FAILED status was still recorded, not skipped


async def test_handle_event_sender_failure_propagates_not_swallowed() -> None:
    """Failure injection: a channel sender exception must propagate, not be
    swallowed — fail-closed default posture (CLAUDE.md §3)."""
    pool = _FakePool()

    async def _boom(_user_id: Any, _event_type: str, _payload: dict[str, Any]) -> bool:
        raise RuntimeError("injected sender failure")

    gateway = NotificationGateway(
        pool=pool,
        senders={NotificationChannel.EMAIL: _boom},
    )

    with pytest.raises(RuntimeError, match="injected sender failure"):
        await gateway.handle_event(
            {"event_type": "marketplace.payment.confirmed", "user_id": str(uuid4())}
        )
