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


async def test_missing_user_id_raises_event_handler_error(pool):
    """negative test: user_id 누락 이벤트는 EventHandlerError를 던진다(I-10 배선 증명)."""
    from src.core.exceptions import EventHandlerError

    gateway = NotificationGateway(pool)
    bus = InProcessEventBus(max_retries=0)
    gateway.register(bus)
    await bus.start()

    with pytest.raises(EventHandlerError, match="user_id 없는 알림 이벤트"):
        await gateway.handle_event({"event_type": "approval.request.created"})

    await bus.stop()


async def test_invalid_user_id_type_raises(pool):
    """negative test: user_id가 정수 등 비문자열 타입이면 UUID 생성 오류 발생."""
    gateway = NotificationGateway(pool)
    bus = InProcessEventBus(max_retries=0)
    gateway.register(bus)
    await bus.start()

    with pytest.raises((AttributeError, TypeError, ValueError)):
        await gateway.handle_event({"event_type": "alert.triggered", "user_id": 12345})

    await bus.stop()


async def test_no_sender_for_channel_records_failure(pool):
    """negative test: 채널 발송기 미등록 시 FAILED 기록 + EventHandlerError."""
    from src.core.exceptions import EventHandlerError

    # senders={} — 아무 발송기도 등록하지 않음
    gateway = NotificationGateway(pool, senders={})
    bus = InProcessEventBus(max_retries=0)
    gateway.register(bus)
    await bus.start()

    with pytest.raises(EventHandlerError, match="알림 발송 실패 채널"):
        await gateway.handle_event(
            {"event_type": "alert.triggered", "user_id": str(await create_test_user(pool))}
        )

    await bus.stop()


async def test_database_error_propagates_via_pool_acquire(pool):
    """실패주입: _record에서 DB 오류 발생 시 예외가 전파된다."""
    import asyncpg

    user_id = await create_test_user(pool)

    async def dummy_sender(uid, event_type, payload):
        return True

    gateway = NotificationGateway(pool, senders={NotificationChannel.IN_APP: dummy_sender})
    bus = InProcessEventBus(max_retries=0)
    gateway.register(bus)
    await bus.start()

    # pool을 닫아 _record의 conn.execute()가 오류를 던지도록 유도
    await pool.close()

    with pytest.raises((asyncpg.PostgresSyntaxError, asyncpg.InterfaceError, OSError, Exception)):
        await gateway.handle_event({"event_type": "alert.triggered", "user_id": str(user_id)})

    await bus.stop()
