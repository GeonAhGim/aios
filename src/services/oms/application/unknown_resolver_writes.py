"""L4-16/L4-27 — UNKNOWN 해소 3분기 확정 쓰기(트랜잭션) + §7.3 escalation 로그.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.2 UNKNOWN 행 3종
(`RESOLVED_AS`/`RESOLVED_ABSENT`/`UNRESOLVED_LIMIT`), §7.3, §9 L4-16/L4-27.

`unknown_resolver.py`(L4-16, 재시도 루프 본체)에서 분리된 모듈이다 —
outbox_dispatcher.py/outbox_commands.py(L4-31)와 같은 이유(파일당 300줄
한도, ADR-2026-09-06-G §10)로, 메트릭/로그 계측(L4-27)을 추가하면서
루프 본체 파일이 한도를 넘겨 이 3개 확정-쓰기 함수 + 해시 헬퍼를 옮겼다.
각 함수는 독립된 트랜잭션 하나를 열고 커밋/롤백까지 스스로 책임진다는
계약은 그대로다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import ALLOWED, OrderEvent, next_status
from src.services.oms.ports.repository import OrderRepoPort

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _payload_hash(order_id: UUID, tag: str) -> str:
    canonical = json.dumps({"order_id": str(order_id), "tag": tag}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def apply_resolved_as(
    pool: asyncpg.Pool, repo: OrderRepoPort, order_id: UUID, found: Order, *, clock: Clock
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current  # 이미 다른 경로로 해소됨(경합) — 멱등 반환
            else:
                target = found.status
                if target not in ALLOWED[OrderStatus.UNKNOWN]:
                    raise InvalidOrderTransitionError(
                        f"find_order_by_client_id가 돌려준 status={target.value}는 "
                        "UNKNOWN에서 허용되는 목적지가 아닙니다 — 어댑터 계약 위반."
                    )
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.RESOLVED_AS.value,
                    reason_code="PROVIDER_LOOKUP_MATCHED",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, f"RESOLVED_AS:{target.value}"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={
                        "filled_quantity": found.filled_quantity,
                        "exchange_order_id": found.exchange_order_id,
                    },
                    event=event,
                )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result


async def apply_resolved_absent(
    pool: asyncpg.Pool, repo: OrderRepoPort, order_id: UUID, *, clock: Clock
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current
            else:
                target = next_status(current.status, OrderEvent.RESOLVED_ABSENT)
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.RESOLVED_ABSENT.value,
                    reason_code="NOT_AT_PROVIDER",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, "RESOLVED_ABSENT"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={},
                    event=event,
                )
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()
    return result


async def escalate(
    pool: asyncpg.Pool,
    repo: OrderRepoPort,
    risk_gate_repo: RiskGateRepository,
    order_id: UUID,
    *,
    max_attempts: int,
    clock: Clock,
) -> OrderView:
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        should_activate = False
        try:
            current = await repo.get_for_update(conn, order_id)
            if current.status is not OrderStatus.UNKNOWN:
                result = current  # 경합 중 이미 해소됨 — 안전통제를 걸 이유가 없다
            else:
                target = next_status(current.status, OrderEvent.UNRESOLVED_LIMIT)
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=OrderStatus.UNKNOWN,
                    to_status=target,
                    event=OrderEvent.UNRESOLVED_LIMIT.value,
                    reason_code="UNKNOWN_RESOLUTION_ATTEMPTS_EXHAUSTED",
                    actor_subject_id="system",
                    trace_id=uuid4(),
                    command_id=None,
                    provider_event_id=None,
                    occurred_at=clock(),
                    payload_hash=_payload_hash(order_id, "UNRESOLVED_LIMIT"),
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.UNKNOWN,
                    expected_version=current.version,
                    new_status=target,
                    patch={},
                    event=event,
                )
                should_activate = True
            ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()

    if should_activate:
        await activate_safety_control(
            risk_gate_repo,
            tenant_id=result.tenant_id,
            actor_subject_id=result.tenant_id,
            actor_is_admin=True,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(result.tenant_id),
            reason=f"OMS_UNKNOWN_ORDER_UNRESOLVED:{order_id}",
        )
        logger.critical(
            "unknown_resolver: order_id=%s 상한(%d회) 초과 — ACCOUNT safety control ACTIVE",
            order_id,
            max_attempts,
            extra={
                "event": "oms.unknown_resolver.escalated",
                "payload": {"order_id": str(order_id), "attempt": max_attempts},
            },
        )
    return result
