"""OMS 리포지토리 포트(L4 명세 §2-C, §9 L4-07).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §5.1.

여기 정의된 6개 `Protocol`은 어댑터(`src/services/oms/adapters/*`, 이후 리프)가
구현할 시그니처의 단일 원천이다. 이 파일 자체는 I/O를 하지 않는다 —
`conn` 인자는 호출자가 이미 연 `asyncpg.Connection`을 그대로 넘긴다는 계약만
표현하고, 실제 SQL은 어댑터가 갖는다. 어댑터가 아직 없는 이유(§9 L4-06
마이그레이션 미착수)는 task-111 note 참조 — 이 리프는 마이그레이션 없이도
정의 가능한 경계만 담는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.data.models.trading import OrderStatus
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent, ProviderOrderEvent
from src.services.oms.contracts.v1_views import OrderView

CommandType = Literal["SUBMIT", "CANCEL", "MODIFY"]
OutboxState = Literal["PENDING", "SENDING", "DONE", "RETRY", "DEAD"]
InboxState = Literal["NEW", "PROCESSED", "IGNORED"]


class OutboxRow(BaseModel):
    """§5.1 outbox claim의 `RETURNING *` 행 모양."""

    id: UUID
    order_id: UUID
    command_type: CommandType
    payload: dict[str, Any]
    state: OutboxState
    attempt: int
    not_before: datetime
    lease_until: datetime | None
    worker_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ClaimResult(BaseModel):
    """§5.2 `order_idempotency` 선점 결과 — NEW/EXISTING/DIGEST_MISMATCH 3분류."""

    kind: Literal["NEW", "EXISTING", "DIGEST_MISMATCH"]
    order_id: UUID | None = None


@runtime_checkable
class OrderRepoPort(Protocol):
    async def transition(
        self,
        conn: asyncpg.Connection,
        *,
        order_id: UUID,
        expected_status: OrderStatus,
        expected_version: int,
        new_status: OrderStatus,
        patch: dict[str, Any],
        event: OrderTransitionEvent,
    ) -> OrderView: ...

    async def get_for_update(self, conn: asyncpg.Connection, order_id: UUID) -> OrderView: ...

    async def find_by_scope_hash(
        self, conn: asyncpg.Connection, scope_hash: str
    ) -> OrderView | None: ...


@runtime_checkable
class OrderEventRepoPort(Protocol):
    async def append(self, conn: asyncpg.Connection, ev: OrderTransitionEvent) -> int: ...

    async def timeline(
        self, conn: asyncpg.Connection, order_id: UUID
    ) -> list[OrderTransitionEvent]: ...


@runtime_checkable
class FillRepoPort(Protocol):
    async def insert_if_absent(self, conn: asyncpg.Connection, fill: FillEvent) -> bool: ...

    async def list_for_order(self, conn: asyncpg.Connection, order_id: UUID) -> list[FillEvent]: ...


@runtime_checkable
class OutboxRepoPort(Protocol):
    async def enqueue(
        self,
        conn: asyncpg.Connection,
        *,
        order_id: UUID,
        command_type: CommandType,
        payload: dict[str, Any],
        not_before: datetime,
    ) -> UUID: ...

    async def claim_batch(
        self, conn: asyncpg.Connection, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]: ...

    async def mark_done(
        self, conn: asyncpg.Connection, id: UUID, *, expected_worker: str
    ) -> None: ...

    async def mark_retry(
        self,
        conn: asyncpg.Connection,
        id: UUID,
        *,
        attempt: int,
        not_before: datetime,
        last_error: str,
        expected_worker: str,
    ) -> None: ...

    async def mark_dead(
        self,
        conn: asyncpg.Connection,
        id: UUID,
        *,
        reason: str,
        expected_worker: str,
    ) -> None: ...


@runtime_checkable
class InboxRepoPort(Protocol):
    async def insert_if_absent(
        self, conn: asyncpg.Connection, ev: ProviderOrderEvent
    ) -> bool: ...

    async def claim_unprocessed(
        self, conn: asyncpg.Connection, *, limit: int
    ) -> list[ProviderOrderEvent]: ...

    async def mark_processed(
        self, conn: asyncpg.Connection, id: UUID, *, expected_state: InboxState = "NEW"
    ) -> None: ...


@runtime_checkable
class IdempotencyRepoPort(Protocol):
    async def claim(
        self,
        conn: asyncpg.Connection,
        *,
        scope_hash: str,
        digest: str,
        order_id: UUID,
        ttl: timedelta,
    ) -> ClaimResult: ...
