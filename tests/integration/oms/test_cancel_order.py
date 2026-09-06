"""L4-17 `application/cancel_order.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-17 DoD("부분체결
후 취소 정합"), §4.2 CANCEL_REQUESTED 행.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_repository import OrderNotFoundError
from src.services.oms.application.cancel_order import cancel_order
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.domain.errors import InvalidOrderTransitionError
from tests.integration.oms.conftest import create_test_user


async def _insert_order(
    pool,
    user_id: uuid.UUID,
    *,
    status: str = "ACKNOWLEDGED",
    order_type: str = "MARKET",
    quantity: Decimal = Decimal("1"),
    filled_quantity: Decimal = Decimal("0"),
    exchange_order_id: str | None = "EX-1",
) -> uuid.UUID:
    price = Decimal("100") if order_type == "LIMIT" else None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity,
                exchange_order_id
            ) VALUES ($1, $2, 'oms-cancel-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      $3, $4, $5, $6, $7, $8)
            RETURNING order_id
            """,
            user_id,
            f"oms-cancel-{uuid.uuid4().hex}",
            order_type,
            quantity,
            price,
            status,
            filled_quantity,
            exchange_order_id,
        )


def _command(order_id: uuid.UUID, tenant_id: uuid.UUID, **overrides: object) -> CancelOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "order_id": order_id,
        "tenant_id": tenant_id,
        "reason": "TEST_CANCEL",
        "actor_subject_id": tenant_id,
        "issued_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return CancelOrderCommand(**defaults)  # type: ignore[arg-type]


async def test_cancel_order_acknowledged_self_loop_and_enqueues_outbox(pool):
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id)

    result = await cancel_order(cmd, pool=pool)

    assert result.status == OrderStatus.ACKNOWLEDGED  # 자기루프 — 상태 불변
    assert result.version == 1
    async with pool.acquire() as conn:
        outbox_row = await conn.fetchrow(
            "SELECT * FROM order_command_outbox WHERE order_id = $1", order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1 AND event = 'CANCEL_REQUESTED'",
            order_id,
        )
    assert outbox_row is not None
    assert outbox_row["command_type"] == "CANCEL"
    assert event_count == 1


async def test_cancel_order_partial_fill_does_not_roll_back_filled_quantity(pool):
    """DoD — 부분체결 정합: 취소가 filled_quantity를 되돌리지 않는다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool,
        user_id,
        status="PARTIALLY_FILLED",
        quantity=Decimal("1"),
        filled_quantity=Decimal("0.4"),
    )
    cmd = _command(order_id, user_id)

    result = await cancel_order(cmd, pool=pool)

    assert result.status == OrderStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.4")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT filled_quantity FROM orders WHERE order_id = $1", order_id
        )
    assert row["filled_quantity"] == Decimal("0.4")


async def test_cancel_order_terminal_order_rejected(pool):
    """negative — 이미 종료된(FILLED) 주문의 취소는 표 밖 전이로 거부, 0행 부작용."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool, user_id, status="FILLED", quantity=Decimal("1"), filled_quantity=Decimal("1")
    )
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await cancel_order(cmd, pool=pool)

    async with pool.acquire() as conn:
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
    assert outbox_count == 0
    assert version == 0


async def test_cancel_order_before_ack_rejected(pool):
    """negative — 아직 거래소 확인 전(SUBMITTED)인 주문은 CANCEL_REQUESTED 표
    밖 전이라 거부된다(§4.2는 ACKNOWLEDGED/PARTIALLY_FILLED만 허용)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="SUBMITTED", exchange_order_id=None)
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await cancel_order(cmd, pool=pool)


async def test_cancel_order_wrong_tenant_raises_not_found(pool):
    """negative — 다른 tenant 소유 주문은 미존재와 동형으로 거부(§8.3)."""
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    order_id = await _insert_order(pool, owner_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, other_id)

    with pytest.raises(OrderNotFoundError):
        await cancel_order(cmd, pool=pool)

    async with pool.acquire() as conn:
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
    assert outbox_count == 0
