"""`order_events` WORM 테이블의 asyncpg 구현(L4 명세 §2-C, §9 L4-07).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §4.1 I6, §4.2,
073beca589d5(L4-06)의 `order_events` DDL·WORM(`worm_sql`).

`append()`는 `order_repository.transition()`이 같은 트랜잭션 안에서
`SET LOCAL oms.event_written='1'` 직후에 호출한다(호출 순서는 이 파일이
아니라 호출부 책임) — 그래야 I6 트리거가 그 UPDATE를 통과시킨다.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from src.data.models.trading import OrderStatus
from src.services.oms.contracts.v1_events import OrderTransitionEvent


def _actor_to_column(actor_subject_id: UUID | Literal["system"]) -> str:
    return str(actor_subject_id)


def _actor_from_column(value: str) -> UUID | Literal["system"]:
    if value == "system":
        return "system"
    return UUID(value)


def _row_to_event(row: asyncpg.Record) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=row["order_id"],
        seq=row["seq"],
        from_status=OrderStatus(row["from_status"]),
        to_status=OrderStatus(row["to_status"]),
        event=row["event"],
        reason_code=row["reason_code"],
        actor_subject_id=_actor_from_column(row["actor_subject_id"]),
        trace_id=row["trace_id"],
        command_id=row["command_id"],
        provider_event_id=row["provider_event_id"],
        occurred_at=row["occurred_at"],
        payload_hash=row["payload_hash"],
    )


class PostgresOrderEventRepository:
    """`OrderEventRepoPort` 구현. 상태 없음 — 매 호출이 `conn`만으로 동작한다."""

    async def append(self, conn: asyncpg.Connection, ev: OrderTransitionEvent) -> int:
        seq = await conn.fetchval(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, event, reason_code,
                actor_subject_id, trace_id, command_id, provider_event_id,
                occurred_at, payload_hash
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING seq
            """,
            ev.order_id,
            ev.from_status.value,
            ev.to_status.value,
            ev.event,
            ev.reason_code,
            _actor_to_column(ev.actor_subject_id),
            ev.trace_id,
            ev.command_id,
            ev.provider_event_id,
            ev.occurred_at,
            ev.payload_hash,
        )
        return int(seq)

    async def timeline(
        self, conn: asyncpg.Connection, order_id: UUID
    ) -> list[OrderTransitionEvent]:
        rows = await conn.fetch(
            "SELECT * FROM order_events WHERE order_id = $1 ORDER BY seq ASC", order_id
        )
        return [_row_to_event(row) for row in rows]
