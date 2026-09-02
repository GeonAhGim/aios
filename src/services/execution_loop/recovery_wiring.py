"""05번 §5.6 재시작 복구 — `recover_pending_orders`를 실제 DB·거래소·이벤트 버스에 배선.

Spec: 05_communication_architecture_v1.2.md#§5.6, src/core/event_bus/recovery.py

`recover_pending_orders`는 순수 오케스트레이션 함수로 세 콜백을 주입받도록
만들어져 있었지만 그 콜백을 실제로 만들어 넘기는 곳이 없어 재시작 복구는
한 번도 실행된 적이 없다(docs/FULL_AUDIT_2026-09-02.md §3). 이 모듈이 그
배선이다. main.py lifespan이 백그라운드 루프를 띄우기 전에 1회 호출한다.

동작:
1. 최종 상태가 아닌 주문(SUBMITTED/ACKNOWLEDGED/PARTIALLY_FILLED, 거래소
   주문ID 있음)을 DB에서 읽는다 — "진실은 항상 DB에 있다"(§5.6).
2. 각 주문을 해당 사용자의 거래소 어댑터로 재조회한다.
3. **FILLED는 DB에 쓰지 않는다.** 체결 반영은 실행 루프 tick의
   `_handle_pending_fill_check`가 `apply_fill` + FSM 전이까지 한 단위로
   처리하는 유일한 경로다. 여기서 먼저 FILLED로 영속화하면 tick이 "이미
   최종 상태"로 보고 FSM 전이를 건너뛰어 실행이 PENDING에 영구히 갇힌다.
4. CANCELLED/REJECTED/EXPIRED/FAILED는 DB에 반영한다(거래소가 이미 끝낸
   주문을 계속 미결로 두지 않기 위해).
5. 상태 변경 여부와 무관하게 `order.status.changed`를 재발행한다 — 재시작으로
   유실됐을 수 있는 이벤트의 재동기화가 §5.6의 목적이다.
6. 재동기화 건수를 audit_log에 남긴다.

한계(후속 leaf): 취소·거부로 끝난 주문의 FSM 상태(BUY/SELL_ORDER_PENDING)를
되돌리는 로직은 cancel.py에도 tick.py에도 없다 — 복구와 무관한 기존 결함.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from src.core.event_bus.recovery import recover_pending_orders
from src.core.logging.audit_log import record_audit_log
from src.data.models.trading import OrderStatus
from src.services.execution_loop.scheduler import AdapterResolver
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn

logger = logging.getLogger(__name__)

_NON_FINAL_STATUSES = ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED")
_PERSISTABLE_TERMINAL = frozenset(
    {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.FAILED}
)
RECOVERY_ACTOR = "main_process"
RECOVERY_ACTION_TYPE = "system.restart_recovery"


async def recover_orders_on_startup(
    pool: asyncpg.Pool,
    *,
    resolve_adapter: AdapterResolver,
    publish: PublishFn,
) -> int:
    async def fetch_pending_orders() -> list[dict[str, Any]]:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT order_id, user_id, exchange, exchange_order_id, status "
                "FROM orders WHERE status = ANY($1::text[]) AND exchange_order_id IS NOT NULL "
                "ORDER BY created_at",
                list(_NON_FINAL_STATUSES),
            )
        return [dict(row) for row in rows]

    async def get_order_status(row: dict[str, Any]) -> dict[str, Any]:
        adapter = await resolve_adapter(row["user_id"], row["exchange"])
        fresh = await adapter.get_order(row["exchange_order_id"])
        async with pool.acquire() as conn:
            current = await repository.get_by_order_id(conn, row["order_id"])
            if current is None:
                raise LookupError(f"복구 중 주문이 사라짐: {row['order_id']}")
            persisted = current
            if fresh.status in _PERSISTABLE_TERMINAL and fresh.status != current.status:
                updated = current.model_copy(
                    update={"status": fresh.status, "filled_quantity": fresh.filled_quantity}
                )
                persisted = await repository.update_from_exchange(
                    conn, updated, expected_status=current.status
                )
        return {
            "order_id": str(persisted.order_id),
            "client_order_id": persisted.client_order_id,
            "execution_id": persisted.execution_id,
            "status": persisted.status.value,
            "exchange_status": fresh.status.value,
            "recovered": True,
        }

    async def republish_order_event(payload: dict[str, Any]) -> None:
        await publish("order.status.changed", payload)

    async def record_recovery(recovered: int) -> None:
        async with pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=RECOVERY_ACTOR,
                action_type=RECOVERY_ACTION_TYPE,
                decision_data={"recovered_orders": recovered},
                target_type="system",
                target_id=RECOVERY_ACTOR,
            )

    return await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
        record_recovery=record_recovery,
    )
