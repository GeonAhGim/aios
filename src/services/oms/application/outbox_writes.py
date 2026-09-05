"""L4-14 — 디스패처 공용 쓰기 프리미티브: outbox 펜스 + 주문 전이 + payload 복원.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1(outbox done/retry/dead
펜스), §5.4(백오프·max_attempts), §7.3(payload_hash — raw 저장 금지).

`outbox_dispatcher.py`와 `outbox_commands.py`가 공유한다. 모든 쓰기는 호출자가
연 트랜잭션 안에서 실행되고, outbox 펜스(`expected_worker`)가 0행이면 어댑터
(L4-08)가 `ConcurrencyConflictError`를 던져 같은 tx의 주문 전이가 함께
롤백된다 — 이 모듈은 그 순서(펜스 먼저)를 강제하지 않고 호출부가 지킨다.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.http_policy import RetryPolicy, backoff_delay
from src.services.oms.application.dispatch_outcome import OutcomeKind, SendOutcome
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort, OutboxRow

Clock = Callable[[], datetime]

DEFAULT_MAX_ATTEMPTS = 6  # §5.4
DEFAULT_CIRCUIT_OPEN_DEFER_SEC = 20.0  # VenueCircuit.open_sec 기본값(§6 F4)
OUTBOX_RETRY_POLICY = RetryPolicy(max_attempts=DEFAULT_MAX_ATTEMPTS, base=1.0, cap=60.0)
NOT_SENT_PREFIX = "NOT_SENT:"
SENT_PREFIX = "SENT:"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OutboxWrites:
    def __init__(
        self,
        *,
        outbox_repo: OutboxRepoPort,
        order_repo: OrderRepoPort,
        worker_id: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_policy: RetryPolicy = OUTBOX_RETRY_POLICY,
        clock: Clock = utcnow,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._outbox = outbox_repo
        self._orders = order_repo
        self.worker_id = worker_id
        self._max_attempts = max_attempts
        self._retry_policy = retry_policy
        self.clock = clock
        self._rng = rng

    async def done(self, conn: asyncpg.Connection, row: OutboxRow) -> None:
        await self._outbox.mark_done(conn, row.id, expected_worker=self.worker_id)

    async def dead(self, conn: asyncpg.Connection, row: OutboxRow, reason: str) -> None:
        await self._outbox.mark_dead(conn, row.id, reason=reason, expected_worker=self.worker_id)

    async def defer(self, conn: asyncpg.Connection, row: OutboxRow, outcome: SendOutcome) -> None:
        """§6 F4/F13 — 회로 OPEN: attempt를 소모하지 않고 not_before만 미룬다."""
        delay = (
            outcome.retry_after_sec
            if outcome.retry_after_sec is not None
            else DEFAULT_CIRCUIT_OPEN_DEFER_SEC
        )
        await self._outbox.mark_retry(
            conn,
            row.id,
            attempt=row.attempt,
            not_before=self.clock() + timedelta(seconds=delay),
            last_error=NOT_SENT_PREFIX + outcome.reason,
            expected_worker=self.worker_id,
        )

    async def retry_or_dead(
        self, conn: asyncpg.Connection, row: OutboxRow, outcome: SendOutcome
    ) -> bool:
        """attempt+1; 상한(§5.4 max_attempts) 도달 시 DEAD. 반환값은 DEAD 여부."""
        attempt = row.attempt + 1
        if attempt >= self._max_attempts:
            await self.dead(conn, row, f"MAX_ATTEMPTS:{outcome.reason}")
            return True
        delay = backoff_delay(self._retry_policy, attempt, outcome.retry_after_sec, self._rng)
        prefix = NOT_SENT_PREFIX if outcome.not_sent else SENT_PREFIX
        await self._outbox.mark_retry(
            conn,
            row.id,
            attempt=attempt,
            not_before=self.clock() + timedelta(seconds=delay),
            last_error=prefix + outcome.reason,
            expected_worker=self.worker_id,
        )
        return False

    async def transition(
        self,
        conn: asyncpg.Connection,
        row: OutboxRow,
        order: OrderView,
        to: OrderStatus,
        event: OrderEvent,
        reason: str | None,
        patch: dict[str, Any],
    ) -> OrderView:
        """`expected_status`·`expected_version`(I5) 조건부 전이 + 이벤트 1행(I6)."""
        ev = OrderTransitionEvent(
            order_id=order.order_id,
            from_status=order.status,
            to_status=to,
            event=event.value,
            reason_code=reason,
            actor_subject_id="system",
            trace_id=uuid_from(row.payload.get("trace_id"), row.id) or row.id,
            command_id=uuid_from(row.payload.get("command_id"), None),
            provider_event_id=None,
            occurred_at=self.clock(),
            payload_hash=payload_hash(row.id, patch),
        )
        return await self._orders.transition(
            conn,
            order_id=order.order_id,
            expected_status=order.status,
            expected_version=order.version,
            new_status=to,
            patch=patch,
            event=ev,
        )


def adopt(existing: Order, reason: str) -> SendOutcome:
    """역조회로 찾은 거래소 주문을 채택한다(§6 F14, §5.4 재전송 전 역조회)."""
    if not existing.exchange_order_id:
        return SendOutcome(OutcomeKind.UNKNOWN, reason + "_WITHOUT_EXCHANGE_ORDER_ID")
    if existing.status is OrderStatus.REJECTED:
        return SendOutcome(OutcomeKind.REJECTED, reason)
    return SendOutcome(OutcomeKind.ACK, reason, exchange_order_id=existing.exchange_order_id)


def order_from_payload(row: OutboxRow, order: OrderView) -> Order | None:
    """payload["order"]를 어댑터용 `Order`로 복원한다. 주문 행과 id·client id가
    다르면 None(fail-closed — 다른 주문의 payload로 전송하지 않는다)."""
    raw = row.payload.get("order")
    if not isinstance(raw, dict):
        return None
    try:
        venue_order = Order.model_validate(raw)
    except ValidationError:
        return None
    if venue_order.order_id != order.order_id:
        return None
    if venue_order.client_order_id != order.client_order_id:
        return None
    return venue_order


def uuid_from(value: object, default: UUID | None) -> UUID | None:
    if value is None:
        return default
    try:
        return UUID(str(value))
    except ValueError:
        return default


def payload_hash(outbox_id: UUID, patch: dict[str, Any]) -> str:
    """§7.3 — raw payload는 저장하지 않는다. 전이 patch의 결정론적 해시만 남긴다."""
    canonical = json.dumps(
        {"outbox_id": str(outbox_id), "patch": patch}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
