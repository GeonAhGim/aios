"""L4-17 — OMS 정정 경로: capability 사전 거부 → 주문 잠금 → MODIFY_REQUESTED
자기루프 전이 → outbox MODIFY enqueue, 단일 tx.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`modify_order(cmd: ModifyOrderCommand, *, pool, profile, clock) -> OrderView`,
§4.2("ACKNOWLEDGED/PARTIALLY_FILLED, MODIFY_REQUESTED, LIMIT & profile.
supports_modify, (불변), outbox MODIFY"), §9 L4-17.

`submit_order.py`(L4-09)와 같은 골격 — 실제 거래소 정정 호출은
`outbox_dispatcher.py`(L4-14, `send_modify`)가 tx 밖에서 수행한다. outbox
payload 계약(L4-14 docstring): `{"changes": {...}}` → 소비 시
`adapter.modify_order(exchange_order_id, **changes)` — 실제 어댑터 3종
(Bitget/KIS/NH)이 읽는 키 이름이 `price`/`size`라 이 리프도 그 이름을 쓴다
(NH만 `quantity`도 하위호환으로 받는다, `exchanges/nh/trading_mixin.py`
docstring 참조 — 레포 내 기존 불일치, 이 리프가 만든 것이 아니다).

편차(해석) — §4.2 표는 MODIFY_REQUESTED를 ACKNOWLEDGED/PARTIALLY_FILLED
양쪽에서 허용한다고 적지만, 이미 병합된 `outbox_commands.send_modify`
(L4-14)는 `order.status is not ACKNOWLEDGED`면 어댑터를 부르지 않고 명령을
그냥 소진한다(무응답 no-op) — PARTIALLY_FILLED에서 정정을 접수하면 outbox
행만 쌓이고 실제로는 절대 처리되지 않는 조용한 실패가 된다. 그 쪽을
고치는 것은 이 리프(L4-17) 범위 밖(L4-14 소유 파일)이라, 여기서는
`state_machine._SIMPLE_TRANSITIONS`가 실제로 정의한 대로 ACKNOWLEDGED만
허용한다(PARTIALLY_FILLED는 `next_status`가 `InvalidOrderTransitionError`로
거부 — 접수 시점에 fail-closed, dispatch 시점의 조용한 no-op보다 안전하다).
후속 리프가 L4-14 쪽을 확장하면 그때 이 제약도 함께 넓힌다.

capability 거부(DoD "NH supports_modify=False 거부"): `profile.supports_modify`
가 거짓이면 DB에 전혀 손대지 않고(연결조차 열지 않음) 즉시
`UnsupportedCapabilityError`(L4-13 `exchanges/common/adapter.py` 규약,
b8b3d6d)를 던진다 — 거래소 호출 0회가 구조적으로 보장된다(이 계층은 애초에
거래소를 호출하지 않는다).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from src.data.models.trading import OrderType
from src.exchanges.common.adapter import UnsupportedCapabilityError
from src.services.oms.adapters.order_repository import OrderNotFoundError, PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.contracts.v1_commands import ModifyOrderCommand
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.state_machine import OrderEvent, next_status
from src.services.oms.domain.venue_profile import VenueCapabilityProfile

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


def _changes(cmd: ModifyOrderCommand) -> dict[str, str]:
    if cmd.new_price is None and cmd.new_quantity is None:
        raise OrderValidationError(
            "MODIFY_NO_CHANGES", "new_price/new_quantity 중 최소 하나는 필수입니다."
        )
    changes: dict[str, str] = {}
    if cmd.new_price is not None:
        changes["price"] = str(cmd.new_price)
    if cmd.new_quantity is not None:
        changes["size"] = str(cmd.new_quantity)
    return changes


async def modify_order(
    cmd: ModifyOrderCommand,
    *,
    pool: asyncpg.Pool,
    profile: VenueCapabilityProfile,
    clock: Clock = utcnow,
) -> OrderView:
    if not profile.supports_modify:
        raise UnsupportedCapabilityError("modify_order", profile.venue)
    changes = _changes(cmd)

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            order = await _orders.get_for_update(conn, cmd.order_id)
            if order.tenant_id != cmd.tenant_id:
                raise OrderNotFoundError(str(cmd.order_id))
            if order.order_type != OrderType.LIMIT:
                raise OrderValidationError(
                    "MARKET_NOT_MODIFIABLE", "시장가 주문은 정정할 수 없습니다."
                )

            new_status = next_status(order.status, OrderEvent.MODIFY_REQUESTED)
            occurred_at = clock()
            event = OrderTransitionEvent(
                order_id=cmd.order_id,
                from_status=order.status,
                to_status=new_status,
                event=OrderEvent.MODIFY_REQUESTED.value,
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
            payload: dict[str, Any] = {
                "changes": changes,
                "trace_id": str(cmd.trace_id),
                "command_id": str(cmd.command_id),
            }
            await _outbox.enqueue(
                conn,
                order_id=cmd.order_id,
                command_type="MODIFY",
                payload=payload,
                not_before=occurred_at,
            )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result
