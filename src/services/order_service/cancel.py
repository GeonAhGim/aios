"""FD-4.3 — 주문 취소.

task-1603(L4-17) 편차 — 이 함수는 더 이상 거래소를 직접 부르지 않는다.
`oms.application.cancel_order`(L4-09 골격 재사용: 단일 tx로 CANCEL_REQUESTED
자기루프 전이 + outbox `CANCEL` enqueue)에 위임만 하고, 실제 거래소 취소는
`outbox_dispatcher`(L4-14, 이미 배선됨)가 tx 밖에서 수행한다. 이 파일은 호출자
시그니처(라우터/Watchdog 등 기존·향후 호출부)를 그대로 유지한 위임 래퍼다
— 판단·전이 로직은 재구현하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.data.models.trading import Order
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.oms.adapters.order_repository import OrderNotFoundError
from src.services.oms.application.cancel_order import cancel_order as _oms_cancel_order
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.domain.errors import OmsError
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn


class OrderCancelError(Exception):
    """대상 주문이 없거나 취소할 수 없는 상태(터미널 등) — 라우터/호출부가
    400/404로 변환."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def cancel_order(
    order_id: UUID,
    *,
    adapter: ExchangeAdapter,  # noqa: ARG001 — 시그니처 호환용(L4-17 이전 호출자), 이제 직접 호출하지 않음
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    reason: str = "ORDER_SERVICE_CANCEL",
) -> Order:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT user_id FROM orders WHERE order_id = $1", order_id)
    if user_id is None:
        raise OrderCancelError(f"존재하지 않는 주문입니다: {order_id}")

    cmd = CancelOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        order_id=order_id,
        tenant_id=user_id,
        reason=reason,
        actor_subject_id="system",
        issued_at=_utcnow(),
    )
    try:
        await _oms_cancel_order(cmd, pool=pool)
    except (OmsError, OrderNotFoundError) as exc:
        raise OrderCancelError(str(exc)) from exc

    async with pool.acquire() as conn:
        persisted = await repository.get_by_order_id(conn, order_id)
    if persisted is None:
        raise OrderCancelError(f"취소 처리 후 재조회에 실패했습니다: {order_id}")

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
