"""L4-16/L4-27 — UNKNOWN-resolution three-way finalized commit writes (transaction) +
§7.3 escalation logging.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.2 the three UNKNOWN row
kinds (`RESOLVED_AS`/`RESOLVED_ABSENT`/`UNRESOLVED_LIMIT`), §7.3, §9 L4-16/L4-27.

Split out of `unknown_resolver.py` (L4-16, the retry-loop body) for the
same reason as outbox_dispatcher.py/outbox_commands.py (L4-31) — a
300-line-per-file cap (ADR-2026-09-06-G §10). Adding metrics/log
instrumentation (L4-27) pushed the loop-body file over the cap, so
these 3 finalized-write functions plus the hash helper were moved here.
Each function still opens exactly one independent transaction and is
solely responsible for its own commit/rollback — that contract is
unchanged.
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
                result = current  # already resolved via another path (race) — idempotent return
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
                # already resolved during the race -- no reason to activate a safety control
                result = current
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
