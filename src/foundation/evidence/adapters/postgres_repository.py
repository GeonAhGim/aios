"""AuditEventRepository의 asyncpg 구현.

Spec: AIOSproject 79번 §1, 105번(동시성 표준) 정신의 INSERT 버전.

`append_event()`가 이 파일의 핵심이다 — 해시 체인은 "이전 이벤트의 hash를
읽고, 그것과 연결된 새 hash를 계산해서 insert"라는 read-then-write지만,
`conditional_update`는 UPDATE 전용이라 여기엔 쓸 수 없다(경합 시 막을
"기존 행"이 없다 — 매번 새 행을 insert하니까). 대신 Postgres advisory lock으로
같은 tenant(또는 system) 체인에 대한 append를 트랜잭션 동안 직렬화한다 —
두 요청이 동시에 "마지막 이벤트"를 같은 것으로 보고 서로 다른 새 이벤트를
그 뒤에 매다는 fork를 막는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import compute_event_hash


def _row_to_event(row: asyncpg.Record) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        sequence_no=row["sequence_no"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_revision=row["aggregate_revision"],
        action=row["action"],
        outcome=Outcome(row["outcome"]),
        actor_subject_id=row["actor_subject_id"],
        trace_id=row["trace_id"],
        payload_hash=row["payload_hash"],
        payload=json.loads(row["payload"]),
        classification=Classification(row["classification"]),
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
        occurred_at=row["occurred_at"],
    )


class PostgresAuditEventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append_event(
        self,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent:
        async with self._pool.acquire() as conn, conn.transaction():
            # 79번 §1 체인 직렬화 지점 — 같은 tenant(또는 system)에 대한
            # append를 이 트랜잭션이 끝날 때까지 블록한다. 두 개의 int4 키를
            # 쓰는 건 다른 advisory lock 용도(예: 다른 bounded context가
            # tenant_id 기반 lock을 또 쓸 때)와 네임스페이스가 섞이지 않게
            # 하기 위해서다(Postgres 관용 패턴).
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('foundation_audit_event'), "
                "hashtext($1))",
                str(tenant_id) if tenant_id is not None else "system",
            )

            if tenant_id is not None:
                prev_row = await conn.fetchrow(
                    "SELECT sequence_no, event_hash FROM foundation_audit_event "
                    "WHERE tenant_id = $1 ORDER BY sequence_no DESC LIMIT 1",
                    tenant_id,
                )
            else:
                prev_row = await conn.fetchrow(
                    "SELECT sequence_no, event_hash FROM foundation_audit_event "
                    "WHERE tenant_id IS NULL ORDER BY sequence_no DESC LIMIT 1"
                )
            next_sequence_no = 1 if prev_row is None else prev_row["sequence_no"] + 1
            previous_hash = None if prev_row is None else prev_row["event_hash"]

            occurred_at = datetime.now(timezone.utc)
            event_hash = compute_event_hash(
                previous_hash=previous_hash,
                tenant_id=tenant_id,
                sequence_no=next_sequence_no,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                action=action,
                outcome=outcome,
                payload_hash=payload_hash,
                classification=classification,
                occurred_at=occurred_at,
            )

            row = await conn.fetchrow(
                "INSERT INTO foundation_audit_event "
                "(id, tenant_id, sequence_no, aggregate_type, aggregate_id, "
                " aggregate_revision, action, outcome, actor_subject_id, trace_id, "
                " payload_hash, payload, classification, previous_hash, event_hash, "
                " occurred_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, "
                "$13, $14, $15, $16) "
                "RETURNING *",
                uuid4(),
                tenant_id,
                next_sequence_no,
                aggregate_type,
                aggregate_id,
                aggregate_revision,
                action,
                outcome.value,
                actor_subject_id,
                trace_id,
                payload_hash,
                json.dumps(payload),
                classification.value,
                previous_hash,
                event_hash,
                occurred_at,
            )
        return _row_to_event(row)

    async def list_timeline(
        self,
        tenant_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        aggregate_type: str | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        conditions = ["tenant_id = $1"]
        params: list[object] = [tenant_id]
        if cursor is not None:
            params.append(int(cursor))
            conditions.append(f"sequence_no < ${len(params)}")
        if aggregate_type is not None:
            params.append(aggregate_type)
            conditions.append(f"aggregate_type = ${len(params)}")
        if action is not None:
            params.append(action)
            conditions.append(f"action = ${len(params)}")
        where_clause = " AND ".join(conditions)
        params.append(limit + 1)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM foundation_audit_event WHERE {where_clause} "  # noqa: S608
                f"ORDER BY sequence_no DESC LIMIT ${len(params)}",
                *params,
            )

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_row_to_event(row) for row in page_rows]
        next_cursor = str(page_rows[-1]["sequence_no"]) if has_more and page_rows else None
        return items, next_cursor

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        async with self._pool.acquire() as conn:
            if tenant_id is not None:
                rows = await conn.fetch(
                    "SELECT * FROM foundation_audit_event WHERE tenant_id = $1 "
                    "ORDER BY sequence_no ASC",
                    tenant_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM foundation_audit_event WHERE tenant_id IS NULL "
                    "ORDER BY sequence_no ASC"
                )
        return [_row_to_event(row) for row in rows]
