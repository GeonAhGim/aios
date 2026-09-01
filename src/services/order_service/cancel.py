"""FD-4.3 — 주문 취소.

트리거: 사용자의 FD-16.3 중지 액션(즉시청산 선택 시), 또는 FD-9.2
Watchdog 판정으로 시스템이 자동 취소.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn


class OrderCancelError(Exception):
    """대상 주문이 없는 경우 등 — 라우터/호출부가 400/404로 변환."""


async def cancel_order(
    order_id: UUID,
    *,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
) -> Order:
    async with pool.acquire() as conn:
        order = await repository.get_by_order_id(conn, order_id)
    if order is None:
        raise OrderCancelError(f"존재하지 않는 주문입니다: {order_id}")

    if order.exchange_order_id is None:
        raise OrderCancelError("거래소에 아직 접수되지 않은 주문은 취소할 수 없습니다.")

    cancelled = await adapter.cancel_order(order.exchange_order_id)
    if not cancelled:
        # 이미 체결된 주문의 취소 시도 등 — 오류로 취급하지 않고 상태
        # 재조회로 전환한다(FD-4.3 예외상황, FD-3.4 재사용은 호출부 책임).
        return order

    updated = order.model_copy(update={"status": OrderStatus.CANCELLED})
    async with pool.acquire() as conn:
        persisted = await repository.update_from_exchange(conn, updated)

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
