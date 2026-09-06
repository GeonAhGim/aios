"""FD-4.4 — 주문 정정(지정가만, 시장가는 정정 불가).

task-1603(L4-17) 편차 — cancel.py와 동일 이유(모듈 상단 참조)로
`oms.application.modify_order`에 위임한다. `venue_profile`은 어댑터의 L4-13
조회 계약(`ExchangeAdapter.venue_profile()`)으로 얻는다 — 아직 구체 어댑터가
그 메서드를 구현하지 않았다면(현재 Bitget/KIS/NH 전부 기본 구현) 그 자체가
`UnsupportedCapabilityError`이고, 이 래퍼는 그것도 `OrderModifyError`로
통일해 던진다(호출자 시그니처 유지 — 이 함수가 던지는 예외 표면은 항상
`OrderModifyError` 하나뿐이었다)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.data.models.trading import Order
from src.exchanges.common.adapter import ExchangeAdapter, UnsupportedCapabilityError
from src.services.oms.adapters.order_repository import OrderNotFoundError
from src.services.oms.application.modify_order import modify_order as _oms_modify_order
from src.services.oms.contracts.v1_commands import ModifyOrderCommand
from src.services.oms.domain.errors import OmsError
from src.services.order_service import repository


class OrderModifyError(Exception):
    """대상 주문이 없거나 정정 불가능한 상태·venue capability 등 — 400/404로 변환."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def modify_order(
    order_id: UUID,
    *,
    new_price: Decimal,
    new_quantity: Decimal,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
) -> Order:
    try:
        profile = adapter.venue_profile()
    except UnsupportedCapabilityError as exc:
        raise OrderModifyError(str(exc)) from exc

    async with pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT user_id FROM orders WHERE order_id = $1", order_id)
    if user_id is None:
        raise OrderModifyError(f"존재하지 않는 주문입니다: {order_id}")

    cmd = ModifyOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        order_id=order_id,
        tenant_id=user_id,
        reason="ORDER_SERVICE_MODIFY",
        actor_subject_id="system",
        issued_at=_utcnow(),
        new_price=new_price,
        new_quantity=new_quantity,
    )
    try:
        await _oms_modify_order(cmd, pool=pool, profile=profile)
    except (OmsError, OrderNotFoundError, UnsupportedCapabilityError) as exc:
        raise OrderModifyError(str(exc)) from exc

    async with pool.acquire() as conn:
        persisted = await repository.get_by_order_id(conn, order_id)
    if persisted is None:
        raise OrderModifyError(f"정정 처리 후 재조회에 실패했습니다: {order_id}")
    return persisted
