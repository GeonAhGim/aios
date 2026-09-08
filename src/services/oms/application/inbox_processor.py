"""L4-15 — inbox 이벤트(중복 전달 흡수) → fills/전이.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`application/inbox_processor.py`, §4.2 FILL 행, §5.1 inbox/fills 쓰기, §6 F9,
§9 L4-15("1000회 중복 → fills 1행; tick의 `_handle_pending_fill_check`가
inbox 경유해도 FSM 전이 동일").

두 공개 메서드는 같은 내부 처리(`_process_row`)를 공유한다:
- `ingest(ev)`: WS/폴링/`order_service.submit.apply_fill`(레거시 호환 래퍼) 등
  "지금 막 받은 이벤트 하나"를 동기 처리해야 하는 호출부용. `insert_if_absent`
  (§5.1 `ON CONFLICT (venue, provider_event_id) DO NOTHING`)로 중복을 흡수한
  뒤, 삽입에 성공한 경우에만 같은 트랜잭션에서 바로 처리한다 — 이미 삽입된
  같은 키의 행이 있으면(중복 전달) `False`를 반환하고 아무것도 다시 하지
  않는다(F9). 1000번 동시에 불러도 Postgres UNIQUE 인덱스가 승자 하나만
  커밋시키므로 fills에도 정확히 1행만 남는다(DoD).
- `process_once(limit)`: 배경 드레인(재시작 후 잔여분·RESYNC 등) — 행을 한
  번에 하나씩 `FOR UPDATE SKIP LOCKED`로 집어(`InboxRepository.
  claim_unprocessed`) 그 자리에서 처리한다. 배치를 한 트랜잭션에 몰아넣지
  않는 이유: 한 행 처리 실패가 다른 행의 롤백으로 번지면 안 되고("실패 시
  행 롤백·재시도 카운트"), SKIP LOCKED 자체가 여러 워커의 동시 `process_once`
  호출에서도 각자 다른 행을 받게 보장한다(outbox 3워커 테스트와 동일 원리,
  다만 outbox처럼 별도 worker_id/lease 컬럼은 쓰지 않는다 — 073beca589d5
  설계 그대로, `inbox_repository.py` 모듈 docstring 참조).

fail-closed 매칭: `client_order_id`/`exchange_order_id`가 있어도 `orders.
exchange`가 이벤트의 `venue`와 다르면(위조·오배선 — 다른 거래소의 내부
식별자가 우연히 같은 문자열일 경우 포함) 적용하지 않고 `IGNORED`로 남긴다.
매칭 자체가 없는 이벤트도 동일(§4.4 "매칭 주문 없음, 대사 대상"). 둘 다
예외를 던지지 않는다 — 위조 이벤트 하나가 배경 드레인 루프 전체를 죽이면
안 된다. 반대로 매칭된 주문의 상태가 `next_status()`가 허용하지 않는
조합이면(예: 아직 SENT되지 않은 주문에 FILL) `InvalidOrderTransitionError`가
그대로 올라가 트랜잭션이 롤백된다 — 이건 데이터가 아니라 배선/타이밍
결함 신호라 조용히 삼키지 않는다(`ConcurrencyConflictError`와 동일한
"올려서 재시도"류 실패로 취급).

체결이 새로 `FILLED`를 만들면(부분체결은 대상 아님, 최종 확정만) 커밋
*이후* 별도 커넥션으로 `position_ledger.record_fill_in_position_ledger`를
1회 호출한다(§FD-4.2-c와 동일 관례 — 원장 반영은 주문 트랜잭션과 원자적
이지 않다, 기존 submit.py/apply_fill()도 항상 그래왔다). 중복 이벤트는애초
`ingest`/`insert_if_absent`가 막아 이 호출 자체가 재실행되지 않는다(DoD
"잔고/포지션 1회 반영").
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.observability.metric_names import OMS_INBOX_DUPLICATE_COUNT_TOTAL
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.trading import OrderStatus
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.inbox_repository import InboxRepository, InboxRow
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent, ProviderOrderEvent
from src.services.oms.domain.fill_normalizer import aggregate
from src.services.oms.domain.state_machine import OrderEvent, is_terminal, next_status
from src.services.oms.ports.repository import FillRepoPort, InboxRepoPort, OrderRepoPort
from src.services.order_service import repository as legacy_order_repository
from src.services.order_service.position_ledger import record_fill_in_position_ledger

logger = logging.getLogger(__name__)

_RESOLVE_ORDER_SQL = """
SELECT order_id FROM orders
WHERE exchange = $1 AND (exchange_order_id = $2 OR client_order_id = $3)
LIMIT 1
"""


def _payload_hash(order_id: UUID, provider_event_id: str) -> str:
    return hashlib.sha256(f"{order_id}:{provider_event_id}".encode()).hexdigest()


class InboxProcessor:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        inbox_repo: InboxRepoPort | None = None,
        fills_repo: FillRepoPort | None = None,
        order_repo: OrderRepoPort | None = None,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._inbox = inbox_repo if inbox_repo is not None else InboxRepository()
        self._fills = fills_repo if fills_repo is not None else FillsRepository()
        self._orders = order_repo if order_repo is not None else PostgresOrderRepository()
        self._metrics = metrics if metrics is not None else NullMetrics()

    async def ingest(self, ev: ProviderOrderEvent) -> bool:
        """새 이벤트 삽입 + 즉시 처리(같은 tx). 중복이면 `False`(F9, 무처리)."""
        filled_order_id: UUID | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await self._inbox.insert_if_absent(conn, ev)
            if not inserted:
                self._metrics.counter(
                    OMS_INBOX_DUPLICATE_COUNT_TOTAL, {"venue": ev.venue, "source": ev.source}
                )
                logger.info(
                    "inbox_processor: 중복 이벤트 흡수 venue=%s event=%s",
                    ev.venue, ev.provider_event_id,
                    extra={
                        "event": "oms.inbox.duplicate",
                        "payload": {"provider_event_id": ev.provider_event_id, "venue": ev.venue},
                    },
                )
                return False
            row_id = await conn.fetchval(
                "SELECT id FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
                ev.venue,
                ev.provider_event_id,
            )
            filled_order_id = await self._process_row(conn, row_id, ev)
        if filled_order_id is not None:
            await self._apply_position_ledger(filled_order_id)
        return True

    async def process_once(self, limit: int = 100) -> int:
        """백로그 드레인 — 행마다 별도 트랜잭션(실패 격리). 처리(성공+무시)
        건수를 반환한다."""
        processed = 0
        filled_order_ids: list[UUID] = []
        for _ in range(limit):
            try:
                claimed, filled_order_id = await self._claim_and_process_one()
            except Exception:
                logger.exception(
                    "inbox_processor: 처리 실패 — 행 롤백, 다음 process_once 주기에 재시도"
                )
                continue
            if not claimed:
                break
            processed += 1
            if filled_order_id is not None:
                filled_order_ids.append(filled_order_id)
        for order_id in filled_order_ids:
            await self._apply_position_ledger(order_id)
        return processed

    async def _claim_and_process_one(self) -> tuple[bool, UUID | None]:
        async with self._pool.acquire() as conn, conn.transaction():
            # `InboxRepoPort.claim_unprocessed`는 계약상 `ProviderOrderEvent`를
            # 돌려주지만 실제 구현(`InboxRepository`)은 PK를 더한 `InboxRow`
            # (공변 반환, inbox_repository.py 모듈 docstring 참조)를 준다.
            rows = cast(
                list[InboxRow], await self._inbox.claim_unprocessed(conn, limit=1)
            )
            if not rows:
                return False, None
            row = rows[0]
            filled_order_id = await self._process_row(conn, row.id, row)
            return True, filled_order_id

    async def _process_row(
        self, conn: asyncpg.Connection, row_id: UUID, ev: ProviderOrderEvent
    ) -> UUID | None:
        """반환값: 이 처리로 새로 `FILLED`가 확정된 order_id(포지션 반영
        대상), 그 외는 `None`."""
        order_id = await self._resolve_order_id(conn, ev)
        if order_id is None:
            await self._mark_ignored(conn, row_id)
            logger.info(
                "inbox_processor: 매칭 주문 없음 venue=%s event=%s", ev.venue, ev.provider_event_id
            )
            return None

        order = await self._orders.get_for_update(conn, order_id)
        if order.exchange != ev.venue:
            # fail-closed — 위조/오배선 이벤트(테넌트·거래소 불일치)는 적용하지 않는다.
            await self._mark_ignored(conn, row_id)
            logger.warning(
                "inbox_processor: venue 불일치(order_id=%s expected=%s got=%s) — 무시",
                order_id,
                order.exchange,
                ev.venue,
                extra={
                    "event": "oms.inbox.venue_mismatch",
                    "payload": {
                        "order_id": str(order_id), "expected_venue": order.exchange,
                        "got_venue": ev.venue, "provider_event_id": ev.provider_event_id,
                    },
                },
            )
            return None

        if is_terminal(order.status):
            await self._inbox.mark_processed(conn, row_id)  # 이미 종결 — 늦은 중복 전달
            return None

        if ev.last_fill is None:
            await self._mark_ignored(conn, row_id)  # 이 리프는 체결 이벤트만 다룬다
            return None

        fill = ev.last_fill.model_copy(update={"order_id": order_id})
        inserted = await self._fills.insert_if_absent(conn, fill)
        if not inserted:
            await self._inbox.mark_processed(conn, row_id)  # fill 자체가 중복(F9)
            return None

        # fills 삽입이 orders.filled_quantity/version을 이미 갱신했다 — 재조회.
        fresh = await self._orders.get_for_update(conn, order_id)
        new_status = next_status(
            fresh.status, OrderEvent.FILL, filled_qty=fresh.filled_quantity, qty=fresh.quantity
        )

        agg = aggregate(await self._fills.list_for_order(conn, order_id))
        patch: dict[str, Any] = {"average_fill_price": agg.avg_price}
        if agg.fee_total:
            # Phase 1 단일 통화 가정(orders.fee_currency는 컬럼 하나) — 다른
            # 모듈(order_service/repository.py 등)과 동일한 편차.
            currency, total = next(iter(agg.fee_total.items()))
            patch["fee_total"] = total
            patch["fee_currency"] = currency

        event = OrderTransitionEvent(
            order_id=order_id,
            from_status=fresh.status,
            to_status=new_status,
            event=OrderEvent.FILL.value,
            reason_code=None,
            actor_subject_id="system",
            trace_id=uuid4(),
            command_id=None,
            provider_event_id=ev.provider_event_id,
            occurred_at=ev.venue_ts,
            payload_hash=_payload_hash(order_id, ev.provider_event_id),
        )
        await self._orders.transition(
            conn,
            order_id=order_id,
            expected_status=fresh.status,
            expected_version=fresh.version,
            new_status=new_status,
            patch=patch,
            event=event,
        )
        await self._inbox.mark_processed(conn, row_id)
        return order_id if new_status is OrderStatus.FILLED else None

    async def _resolve_order_id(
        self, conn: asyncpg.Connection, ev: ProviderOrderEvent
    ) -> UUID | None:
        if ev.exchange_order_id is None and ev.client_order_id is None:
            return None
        result = await conn.fetchval(
            _RESOLVE_ORDER_SQL, ev.venue, ev.exchange_order_id, ev.client_order_id
        )
        return cast("UUID | None", result)

    async def _mark_ignored(self, conn: asyncpg.Connection, row_id: UUID) -> None:
        await conditional_update(
            conn,
            table="provider_event_inbox",
            id_column="id",
            id_value=row_id,
            expected_state_column="state",
            expected_state_value="NEW",
            set_values={"state": "IGNORED", "processed_at": datetime.now(timezone.utc)},
            returning="id",
        )

    async def _apply_position_ledger(self, order_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            full_order = await legacy_order_repository.get_by_order_id(conn, order_id)
        if full_order is None:
            logger.warning(
                "inbox_processor: FILLED 확정 뒤 order_id=%s 조회 실패 — position_ledger 생략",
                order_id,
            )
            return
        await record_fill_in_position_ledger(self._pool, full_order, metrics=self._metrics)
