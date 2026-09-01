"""FD-4.2 — 주문 전송(멱등성 확인 → 거래소 전송 → DB 영속화 → 이벤트 발행).

Spec: 기능설계문서_v1.21.md#FD-4.2

트리거: FD-8.4(Executor)가 매매 판단을 내린 직후. 판단(주문을 낼지 말지,
얼마나)은 FD-8의 책임이고, 이 함수는 "이미 승인된 주문을 어떻게 안전하게
전송·추적하는가"만 다룬다(8.2-A 경계선).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class OrderSubmissionError(Exception):
    """FD-4.1 검증 실패 등 — 거래소 호출 전 단계에서 이미 거부된 경우.
    Executor가 아니라 상위(FD-8.2 Allocation) 로직 버그 신호."""


async def submit_order(
    order: Order,
    *,
    user_id: UUID,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
) -> Order:
    async with pool.acquire() as conn:
        # FD-4.2-a 멱등성 사전 확인 — 동일 client_order_id가 이미 있으면
        # 재전송 아님, 기존 상태 재확인으로 전환(실제 거래소 호출 생략).
        existing = await repository.get_by_client_order_id(conn, order.client_order_id)
        if existing is not None:
            return existing

    # FD-4.2-b 거래소 전송 — REJECTED는 예외가 아니라 정상 흐름(place_order가
    # status=REJECTED로 반환). 네트워크 오류(RetryableExchangeError)는 그대로
    # 전파한다 — 재시도 전 반드시 이 함수를 처음부터(멱등성 확인부터)
    # 다시 거쳐야 하므로 이 함수 내부에서 자체 재시도하지 않는다.
    submitted = await adapter.place_order(order)

    # FD-4.2-c DB 영속화 — 이벤트 발행보다 먼저 커밋(05번 §5.6).
    async with pool.acquire() as conn:
        persisted = await repository.insert(conn, submitted, user_id=user_id)

    # FD-4.2-d 이벤트 발행(FD-6.1 재사용).
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


async def apply_fill(
    order: Order,
    *,
    exchange_order_id: str,
    filled_quantity: Any,
    average_fill_price: Any,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
) -> Order:
    """제출 직후(동기 체결) 또는 이후 폴링(FD-3.4)으로 체결이 확인됐을 때
    상태를 FILLED로 갱신한다 — Executor.execute()와 실행 루프(오케스트레이터)
    양쪽이 공유하는 갱신 경로(FD-8.4 처리단계 5의 전제)."""
    updated = order.model_copy(
        update={
            "exchange_order_id": exchange_order_id,
            "status": OrderStatus.FILLED,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
        }
    )
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
