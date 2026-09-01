"""FD-4.4 — 주문 정정(지정가만, 시장가는 정정 불가).

트리거: FD-8.4(FROZEN-PAPER-ONLY)가 기존 지정가 주문의 가격을 재산정해야
한다고 판단할 때.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository


class OrderModifyError(Exception):
    """대상 주문이 없거나 시장가 주문 정정 시도 등 — 400/404로 변환."""


async def modify_order(
    order_id: UUID,
    *,
    new_price: Decimal,
    new_quantity: Decimal,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
) -> Order:
    async with pool.acquire() as conn:
        order = await repository.get_by_order_id(conn, order_id)
    if order is None:
        raise OrderModifyError(f"존재하지 않는 주문입니다: {order_id}")

    if order.order_type != OrderType.LIMIT:
        # FD-4.1 사전 검증 — 거래소까지 안 가고 즉시 거부.
        raise OrderModifyError("시장가 주문은 정정할 수 없습니다.")
    if order.exchange_order_id is None:
        raise OrderModifyError("거래소에 아직 접수되지 않은 주문은 정정할 수 없습니다.")

    modified = await adapter.modify_order(
        order.exchange_order_id, price=str(new_price), size=str(new_quantity)
    )
    # 거래소 응답(modified)에는 이 시스템 전용 식별자(client_order_id 등)가
    # 없을 수 있다(Bitget.get_order()와 동일 원칙) — 원본 order를 기준으로
    # 거래소가 실제로 확인해준 값(상태·거래소 주문ID)만 덮어쓴다.
    updated = order.model_copy(
        update={
            "exchange_order_id": modified.exchange_order_id,
            "price": Money(amount=new_price, currency=Currency.USDT),
            "quantity": new_quantity,
            "status": modified.status,
        }
    )
    async with pool.acquire() as conn:
        return await repository.update_after_modify(conn, updated)
