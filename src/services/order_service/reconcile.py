"""FD-4.5 — UNKNOWN 상태 재조회.

8.3 원칙 — UNKNOWN을 실패로 단정하지 않는다. 최대 3회, 2초 간격으로
FD-3.4(get_order)를 재호출해 최종 상태를 확정한다. 3회 후에도 UNKNOWN이면
자동 해소를 포기하고 CRITICAL 로그로 Human 개입을 신호한다.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_INTERVAL_SECONDS = 2.0

SleepFn = Callable[[float], Awaitable[None]]


async def resolve_unknown(
    order_id: UUID,
    *,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> Order:
    async with pool.acquire() as conn:
        order = await repository.get_by_order_id(conn, order_id)
    if order is None:
        raise ValueError(f"존재하지 않는 주문입니다: {order_id}")
    if order.exchange_order_id is None:
        raise ValueError("거래소 주문ID가 없는 주문은 재조회할 수 없습니다.")

    current = order
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reconfirmed = await adapter.get_order(current.exchange_order_id)  # type: ignore[arg-type]
        if reconfirmed.status != OrderStatus.UNKNOWN:
            updated = current.model_copy(
                update={
                    "status": reconfirmed.status,
                    "filled_quantity": reconfirmed.filled_quantity,
                }
            )
            async with pool.acquire() as conn:
                persisted = await repository.update_from_exchange(
                    conn, updated, expected_status=current.status
                )
            if publish is not None:
                await publish(
                    "order.status.changed",
                    {
                        "order_id": str(persisted.order_id),
                        "client_order_id": persisted.client_order_id,
                        "execution_id": persisted.execution_id,
                        "status": persisted.status.value,
                    },
                )
            return persisted

        if attempt < MAX_ATTEMPTS:
            await sleep(RETRY_INTERVAL_SECONDS)

    logger.critical(
        "FD-4.5: 주문 상태 UNKNOWN 자동 해소 실패(order_id=%s, %d회 재조회 후에도 미확정) — "
        "Human 개입 필요",
        order_id,
        MAX_ATTEMPTS,
    )
    return current
