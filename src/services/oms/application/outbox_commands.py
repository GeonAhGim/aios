"""L4-14/L4-31 — outbox CANCEL/MODIFY command submission
(`OutboxDispatcher._send_cancel/_send_modify` body).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.2
(CANCEL_REQUESTED/MODIFIED), §4.4 (outbox row state-machine cross-reference
— this module directly writes `DONE`/`DEAD`/`RETRY` transitions), §5.4
(idempotent-family retry), §6 F8. §2-C/§9 L4-31: this file is a module
split out of `outbox_dispatcher.py` (L4-14), retroactively registering a
row the spec did not have (ADR-2026-09-06-G §10).

SUBMIT과 달리 취소·정정은 멱등 계열이라 응답 유실 시 같은 명령을 다시 보내도
안전하다(`classify_idempotent_failure`). 취소 결과 상태(`VENUE_CANCELLED`)는
여기서 전이하지 않는다 — 거래소 이벤트가 inbox(L4-15)로 들어와 확정한다.
정정은 응답의 price/qty를 `MODIFIED` 이벤트로 즉시 반영한다(§4.2).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from src.data.models.trading import OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    SendOutcome,
    classify_idempotent_failure,
)
from src.services.oms.application.outbox_writes import OutboxWrites
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent, is_terminal
from src.services.oms.ports.repository import OrderRepoPort, OutboxRow

AdapterResolver = Callable[[UUID, str], Awaitable[ExchangeAdapter]]


@dataclass
class CommandCounters:
    """디스패처 `DispatchReport`의 부분집합 — 이 모듈이 갱신하는 카운터만."""

    acknowledged: int = 0
    completed: int = 0  # 명령 소진(주문 전이 없음)
    retried: int = 0
    deferred: int = 0
    dead: int = 0


async def finalize_command(
    writes: OutboxWrites,
    conn: asyncpg.Connection,
    row: OutboxRow,
    outcome: SendOutcome,
    counters: CommandCounters,
) -> None:
    """CANCEL/MODIFY 공통 마무리 — 주문 전이 없음."""
    kind = outcome.kind
    if kind is OutcomeKind.DONE:
        await writes.done(conn, row)
        counters.completed += 1
    elif kind is OutcomeKind.DEAD:
        await writes.dead(conn, row, outcome.reason)
        counters.dead += 1
    elif kind is OutcomeKind.DEFER:
        await writes.defer(conn, row, outcome)
        counters.deferred += 1
    elif await writes.retry_or_dead(conn, row, outcome):
        counters.dead += 1
    else:
        counters.retried += 1


async def send_cancel(
    *,
    pool: asyncpg.Pool,
    writes: OutboxWrites,
    orders: OrderRepoPort,
    resolve_adapter: AdapterResolver,
    row: OutboxRow,
    counters: CommandCounters,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        order: OrderView = await orders.get_for_update(conn, row.order_id)
        if is_terminal(order.status):
            await writes.done(conn, row)
            counters.completed += 1
            return
        if not order.exchange_order_id:  # ACK 전 — 취소할 거래소 id가 아직 없다
            pending = SendOutcome(OutcomeKind.RETRY, "AWAITING_ACK", not_sent=True)
            await finalize_command(writes, conn, row, pending, counters)
            return
    adapter = await resolve_adapter(order.tenant_id, order.exchange)
    try:
        accepted = await adapter.cancel_order(order.exchange_order_id)
    except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
        outcome = classify_idempotent_failure(exc)
    else:
        outcome = SendOutcome(
            OutcomeKind.DONE, "CANCEL_ACCEPTED" if accepted else "CANCEL_NOT_ACCEPTED"
        )
    async with pool.acquire() as conn, conn.transaction():
        await finalize_command(writes, conn, row, outcome, counters)


async def send_modify(
    *,
    pool: asyncpg.Pool,
    writes: OutboxWrites,
    orders: OrderRepoPort,
    resolve_adapter: AdapterResolver,
    row: OutboxRow,
    counters: CommandCounters,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        order: OrderView = await orders.get_for_update(conn, row.order_id)
        changes = row.payload.get("changes")
        if order.status is not OrderStatus.ACKNOWLEDGED or not order.exchange_order_id:
            await writes.done(conn, row)  # 정정 가능한 상태가 아니다 — 명령 소진
            counters.completed += 1
            return
        if not isinstance(changes, dict) or not changes:
            await writes.dead(conn, row, "PAYLOAD_INVALID")
            counters.dead += 1
            return
    adapter = await resolve_adapter(order.tenant_id, order.exchange)
    patch: dict[str, Any] = {}
    try:
        modified = await adapter.modify_order(order.exchange_order_id, **changes)
    except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
        outcome = classify_idempotent_failure(exc)
    else:
        outcome = SendOutcome(OutcomeKind.ACK, "VENUE_MODIFIED")
        patch = {
            "quantity": modified.quantity,
            "price": modified.price.amount if modified.price is not None else None,
        }
    async with pool.acquire() as conn, conn.transaction():
        if outcome.kind is OutcomeKind.ACK:
            await writes.done(conn, row)  # 펜스 먼저(§5.1) — 0행이면 아래 전이는 롤백
            await writes.transition(
                conn, row, order, OrderStatus.ACKNOWLEDGED, OrderEvent.MODIFIED,
                outcome.reason, patch,
            )
            counters.acknowledged += 1
        else:
            await finalize_command(writes, conn, row, outcome, counters)
