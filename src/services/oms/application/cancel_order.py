"""L4-17 — OMS 취소 경로: 주문 잠금 → CANCEL_REQUESTED 자기루프 전이 →
outbox CANCEL enqueue, 단일 tx.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`cancel_order(cmd: CancelOrderCommand, *, pool, clock) -> OrderView`,
§4.2("ACKNOWLEDGED/PARTIALLY_FILLED, CANCEL_REQUESTED, 터미널 아님, (불변),
outbox CANCEL"), §9 L4-17.

`submit_order.py`(L4-09)와 같은 골격(단일 tx: 잠금→전이→outbox enqueue, 거래소는
직접 부르지 않는다)을 재사용한다 — 실제 CANCEL 호출은 `outbox_dispatcher.py`
(L4-14, `send_cancel`)가 tx 밖에서 수행한다.

`CANCEL_REQUESTED`는 상태를 바꾸지 않는 자기루프 이벤트다(§3.3 — 신규 이벤트가
`orders.status` 동결 계약을 건드리지 않도록). `state_machine.next_status()`가
이미 이 자기루프를 표현한다: ACKNOWLEDGED/PARTIALLY_FILLED에서만 허용되고,
그 외(CREATED/VALIDATED/SUBMITTED/UNKNOWN — 아직 거래소 확정 전이거나 이미
불확실한 상태) 또는 터미널 상태에서는 `InvalidOrderTransitionError`로
fail-closed 거부된다(DoD "이미 종료된 주문의 취소는 표 밖 전이로 거부").

부분체결 정합(DoD): 이 함수의 `patch`는 항상 `{}`다 — `filled_quantity`를
전혀 건드리지 않으므로 PARTIALLY_FILLED 주문의 취소는 이미 체결된 수량을
되돌리지 않고 그대로 둔 채 취소 요청만 기록한다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.services.oms.adapters.order_repository import OrderNotFoundError, PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent, next_status

Clock = Callable[[], datetime]

_orders = PostgresOrderRepository()
_outbox = OutboxRepository()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _event_payload_hash(order_id: UUID, command_id: UUID) -> str:
    canonical = json.dumps(
        {"order_id": str(order_id), "command_id": str(command_id)}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def cancel_order(
    cmd: CancelOrderCommand, *, pool: asyncpg.Pool, clock: Clock = utcnow
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            order = await _orders.get_for_update(conn, cmd.order_id)
            if order.tenant_id != cmd.tenant_id:
                # §8.3 404 동형 — 다른 tenant 소유 주문은 미존재와 구분하지 않는다
                # (order_query.py와 동일 관례).
                raise OrderNotFoundError(str(cmd.order_id))

            new_status = next_status(order.status, OrderEvent.CANCEL_REQUESTED)
            occurred_at = clock()
            event = OrderTransitionEvent(
                order_id=cmd.order_id,
                from_status=order.status,
                to_status=new_status,
                event=OrderEvent.CANCEL_REQUESTED.value,
                reason_code=cmd.reason,
                actor_subject_id=cmd.actor_subject_id,
                trace_id=cmd.trace_id,
                command_id=cmd.command_id,
                provider_event_id=None,
                occurred_at=occurred_at,
                payload_hash=_event_payload_hash(cmd.order_id, cmd.command_id),
            )
            result = await _orders.transition(
                conn,
                order_id=cmd.order_id,
                expected_status=order.status,
                expected_version=order.version,
                new_status=new_status,
                patch={},
                event=event,
            )
            await _outbox.enqueue(
                conn,
                order_id=cmd.order_id,
                command_type="CANCEL",
                payload={
                    "trace_id": str(cmd.trace_id),
                    "command_id": str(cmd.command_id),
                    "reason": cmd.reason,
                },
                not_before=occurred_at,
            )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result
