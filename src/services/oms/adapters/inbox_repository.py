"""`provider_event_inbox` Postgres 어댑터(L4 명세 §9 L4-08).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §5.1 inbox 행,
§6 F9(중복 전달 흡수).

`insert_if_absent`는 §5.1 표 그대로 `INSERT ... ON CONFLICT (venue,
provider_event_id) DO NOTHING` — 같은 이벤트가 WS 재전송·폴링 중복으로 몇 번
와도 `RETURNING`이 비면(False) 무시한다(카운트 SELECT로 판정하지 않는다).

`claim_unprocessed`는 `InboxRow`(이 모듈 전용, `ProviderOrderEvent` +
`id`)를 돌려준다 — 포트가 반환 타입을 `ProviderOrderEvent`로 선언하지만
`mark_processed(id)`를 호출하려면 행 PK가 필요하다. `InboxRow`는
`ProviderOrderEvent`의 구조적 상위집합(공변 반환)이라 `InboxRepoPort`
`runtime_checkable` 검사와 타입 검사 양쪽을 만족한다. `FOR UPDATE SKIP
LOCKED`는 호출자가 커밋 전까지 붙잡는 트랜잭션 범위 안에서만 여러 워커가
서로 다른 행을 받게 한다(outbox처럼 별도 worker_id/lease 컬럼을 쓰지 않는
이유 — 스키마 자체가 그렇게 설계됐다, 073beca589d5 참조).

`mark_processed`는 `expected_state`(기본 `NEW`) 조건부 UPDATE — 105번
헬퍼(`conditional_update`)를 그대로 쓴다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from src.services.oms.ports.repository import InboxState

_CLAIM_UNPROCESSED_SQL = """
SELECT id, venue, provider_event_id, venue_symbol, exchange_order_id, client_order_id,
       venue_status, filled_quantity, average_price, last_fill, venue_ts, received_at,
       source, raw_hash
FROM provider_event_inbox
WHERE state = 'NEW'
ORDER BY received_at
LIMIT $1
FOR UPDATE SKIP LOCKED
"""


class InboxRow(ProviderOrderEvent):
    """`claim_unprocessed`가 돌려주는 행 — PK(`id`)를 더한 구조적 상위집합."""

    id: UUID


def _row_to_inbox_row(record: asyncpg.Record) -> InboxRow:
    last_fill = record["last_fill"]
    return InboxRow(
        id=record["id"],
        provider_event_id=record["provider_event_id"],
        venue=record["venue"],
        venue_symbol=record["venue_symbol"],
        exchange_order_id=record["exchange_order_id"],
        client_order_id=record["client_order_id"],
        venue_status=record["venue_status"],
        filled_quantity=record["filled_quantity"],
        average_price=record["average_price"],
        last_fill=FillEvent.model_validate(json.loads(last_fill)) if last_fill else None,
        venue_ts=record["venue_ts"],
        received_at=record["received_at"],
        source=record["source"],
        raw_hash=record["raw_hash"],
    )


class InboxRepository:
    """`InboxRepoPort` 구현체 — I/O 전부 이 클래스 안에만 있다."""

    async def insert_if_absent(
        self, conn: asyncpg.Connection, ev: ProviderOrderEvent
    ) -> bool:
        last_fill = (
            json.dumps(ev.last_fill.model_dump(mode="json")) if ev.last_fill is not None else None
        )
        row = await conn.fetchval(
            "INSERT INTO provider_event_inbox ("
            "venue, provider_event_id, venue_symbol, exchange_order_id, client_order_id, "
            "venue_status, filled_quantity, average_price, last_fill, venue_ts, received_at, "
            "source, raw_hash"
            ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13) "
            "ON CONFLICT (venue, provider_event_id) DO NOTHING RETURNING id",
            ev.venue,
            ev.provider_event_id,
            ev.venue_symbol,
            ev.exchange_order_id,
            ev.client_order_id,
            ev.venue_status,
            ev.filled_quantity,
            ev.average_price,
            last_fill,
            ev.venue_ts,
            ev.received_at,
            ev.source,
            ev.raw_hash,
        )
        return row is not None

    async def claim_unprocessed(
        self, conn: asyncpg.Connection, *, limit: int
    ) -> list[InboxRow]:
        records = await conn.fetch(_CLAIM_UNPROCESSED_SQL, limit)
        return [_row_to_inbox_row(r) for r in records]

    async def mark_processed(
        self, conn: asyncpg.Connection, id: UUID, *, expected_state: InboxState = "NEW"
    ) -> None:
        # `conditional_update`(105번 헬퍼)는 값을 파이썬에서 바인딩하므로 SQL
        # `now()`를 못 쓴다 — 호출 시점의 UTC를 여기서 계산해 넘긴다.
        await conditional_update(
            conn,
            table="provider_event_inbox",
            id_column="id",
            id_value=id,
            expected_state_column="state",
            expected_state_value=expected_state,
            set_values={"state": "PROCESSED", "processed_at": datetime.now(timezone.utc)},
            returning="id",
        )
