"""17.1/17.5 통합테스트 — EventBus 연동 + 실제 dev DB 기록 검증."""
import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality
from src.core.notifications.channel_policy import NotificationChannel
from src.core.notifications.gateway import NotificationGateway
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _history(pool, user_id):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT event_type, channel, status FROM notifications WHERE user_id = $1", user_id
        )


async def test_forced_event_sends_all_forced_channels_and_records(pool):
    user_id = await create_test_user(pool)
    sent: list[NotificationChannel] = []

    async def send_email(uid, event_type, payload):
        sent.append(NotificationChannel.EMAIL)
        return True

    async def send_push(uid, event_type, payload):
        sent.append(NotificationChannel.PUSH)
        return True

    gateway = NotificationGateway(
        pool, senders={NotificationChannel.EMAIL: send_email, NotificationChannel.PUSH: send_push}
    )
    bus = InProcessEventBus(max_retries=1, retry_initial_delay_seconds=0.01)
    gateway.register(bus)
    await bus.start()

    await bus.publish(
        "approval.request.created",
        {"event_type": "approval.request.created", "user_id": str(user_id)},
    )
    await asyncio.sleep(0.05)
    await bus.stop()

    assert set(sent) == {NotificationChannel.EMAIL, NotificationChannel.PUSH}
    rows = await _history(pool, user_id)
    assert {r["channel"] for r in rows} == {"EMAIL", "PUSH"}
    assert all(r["status"] == "SENT" for r in rows)


async def test_channel_send_failure_escalates_via_event_bus_critical_path(pool):
    """EventBus의 CRITICAL 재시도(§4.5) 재사용 검증 — 발송이 계속 실패하면
    최종적으로 event_bus.handler.escalated로 격상된다(FD-17.1 exception 원칙)."""
    from src.core.event_bus.in_process import HANDLER_ESCALATED_TOPIC

    user_id = await create_test_user(pool)

    async def always_fail(uid, event_type, payload):
        return False

    gateway = NotificationGateway(pool, senders={NotificationChannel.EMAIL: always_fail})
    bus = InProcessEventBus(max_retries=1, retry_initial_delay_seconds=0.01)
    gateway.register(bus)

    escalated = []

    async def on_escalated(payload):
        escalated.append(payload)

    bus.subscribe(HANDLER_ESCALATED_TOPIC, on_escalated, criticality=HandlerCriticality.SAFE)
    await bus.start()

    await bus.publish(
        "marketplace.purchase.requested",
        {"event_type": "marketplace.purchase.requested", "user_id": str(user_id)},
    )
    await asyncio.sleep(0.2)
    await bus.stop()

    assert len(escalated) == 1
    rows = await _history(pool, user_id)
    assert all(r["status"] == "FAILED" for r in rows)
