"""`order_command_outbox` Postgres 어댑터(L4 명세 §9 L4-08).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §5.1 outbox 행.

`claim_batch`는 §5.1 표의 SQL을 그대로 쓴다 — 서브쿼리의 `FOR UPDATE SKIP
LOCKED`가 여러 워커의 동시 클레임에서 겹치는 행을 없앤다(다중 워커 안전).
바깥쪽 `UPDATE ... RETURNING`은 행 순서를 보장하지 않으므로 CTE로 감싸
`created_at` 기준으로 다시 정렬한다.

`mark_done/retry/dead`는 `WHERE id=$1 AND state='SENDING' AND
worker_id=$expected_worker`(§5.1) — 0행이면 리스를 잃은 워커의 늦은 쓰기다.
이미 `outbox_dispatcher.py`가 이 상황을 잡으려고 재사용 중인
`ConcurrencyConflictError`(core/db/conditional_write, 105번 §2)를 그대로
던진다 — 새 예외 클래스를 추가하지 않는다(기존 클래스 우선).

`payload`는 JSONB인데 asyncpg는 jsonb 코덱을 자동 등록하지 않는다(이
프로젝트의 다른 어댑터도 동일, 예: `src/core/idempotency.py`) — 쓸 때
`json.dumps` + `$N::jsonb`, 읽을 때 `json.loads`를 수동으로 맞춘다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.services.oms.ports.repository import CommandType, OutboxRow

_CLAIM_SQL = """
WITH claimed AS (
    UPDATE order_command_outbox
    SET state = 'SENDING', worker_id = $1,
        lease_until = now() + make_interval(secs => $2::double precision),
        updated_at = now()
    WHERE id IN (
        SELECT id FROM order_command_outbox
        WHERE state = 'PENDING' AND not_before <= now()
        ORDER BY created_at
        LIMIT $3
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *
)
SELECT * FROM claimed ORDER BY created_at
"""


def _row_to_outbox_row(record: asyncpg.Record) -> OutboxRow:
    payload = record["payload"]
    return OutboxRow(
        id=record["id"],
        order_id=record["order_id"],
        command_type=record["command_type"],
        payload=json.loads(payload) if isinstance(payload, str) else payload,
        state=record["state"],
        attempt=record["attempt"],
        not_before=record["not_before"],
        lease_until=record["lease_until"],
        worker_id=record["worker_id"],
        last_error=record["last_error"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


class OutboxRepository:
    """`OutboxRepoPort` 구현체 — I/O 전부 이 클래스 안에만 있다."""

    async def enqueue(
        self,
        conn: asyncpg.Connection,
        *,
        order_id: UUID,
        command_type: CommandType,
        payload: dict[str, Any],
        not_before: datetime,
    ) -> UUID:
        row_id: UUID = await conn.fetchval(
            "INSERT INTO order_command_outbox (order_id, command_type, payload, not_before) "
            "VALUES ($1, $2, $3::jsonb, $4) RETURNING id",
            order_id,
            command_type,
            json.dumps(payload),
            not_before,
        )
        return row_id

    async def claim_batch(
        self, conn: asyncpg.Connection, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        records = await conn.fetch(_CLAIM_SQL, worker_id, lease_sec, limit)
        return [_row_to_outbox_row(r) for r in records]

    async def mark_done(
        self, conn: asyncpg.Connection, id: UUID, *, expected_worker: str
    ) -> None:
        await self._fence(
            conn, id, expected_worker, set_clause="state = 'DONE', updated_at = now()"
        )

    async def mark_retry(
        self,
        conn: asyncpg.Connection,
        id: UUID,
        *,
        attempt: int,
        not_before: datetime,
        last_error: str,
        expected_worker: str,
    ) -> None:
        await self._fence(
            conn,
            id,
            expected_worker,
            set_clause="state = 'PENDING', attempt = $3, not_before = $4, last_error = $5, "
            "worker_id = NULL, lease_until = NULL, updated_at = now()",
            extra_params=(attempt, not_before, last_error),
        )

    async def mark_dead(
        self,
        conn: asyncpg.Connection,
        id: UUID,
        *,
        reason: str,
        expected_worker: str,
    ) -> None:
        await self._fence(
            conn,
            id,
            expected_worker,
            set_clause="state = 'DEAD', last_error = $3, updated_at = now()",
            extra_params=(reason,),
        )

    async def _fence(
        self,
        conn: asyncpg.Connection,
        id: UUID,
        expected_worker: str,
        *,
        set_clause: str,
        extra_params: tuple[Any, ...] = (),
    ) -> None:
        row = await conn.fetchrow(
            f"UPDATE order_command_outbox SET {set_clause} "  # noqa: S608 -- 상수 set_clause
            "WHERE id = $1 AND state = 'SENDING' AND worker_id = $2 "
            "RETURNING id",
            id,
            expected_worker,
            *extra_params,
        )
        if row is None:
            raise ConcurrencyConflictError(
                f"order_command_outbox.id={id}: state!='SENDING' 또는 "
                f"worker_id!={expected_worker!r}(리스를 잃은 워커의 늦은 쓰기)"
            )
