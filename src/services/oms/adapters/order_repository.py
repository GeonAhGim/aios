"""`orders` 행 전이·조회의 asyncpg 구현(L4 명세 §2-C, §9 L4-07).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `adapters/
order_repository.py`, §4.1 I2/I5/I6, §4.2 전이표, §5.1 `orders` 전이 잠금.

`transition()` 순서(§5.1 그대로): `get_for_update`(FOR UPDATE로 행 잠금) →
`state_machine.ALLOWED`로 전이 검증(L4-02, DB 트리거보다 먼저 fail-closed) →
`SET LOCAL oms.event_written='1'` → `order_events` INSERT(L4-06 I6 트리거가
요구) → `conditional_update`(status **AND** version=$expected, 105번 §2.1
확장 `extra_conditions`) → `audit_bridge.emit`(같은 tx, L0-4). 이미
`get_for_update`로 행을 잠근 뒤라 `conditional_update`의 WHERE는 사실상 항상
매치한다 — 그래도 DB 트리거(I5 버전 자동 증가·I6 이벤트 동반)와 이중으로
검증되는 게 낫다는 105번/L4-06 설계 그대로 유지한다(벨트+서스펜더).

호출부가 읽은 뒤 잠글 새가 없이(get_for_update를 거치지 않고) 직접
`expected_status`/`expected_version`을 들고 온 경우에도 안전하다 —
`get_for_update`가 다시 실제 행을 잠그고 읽어 그 값과 비교하므로, 오래된
스냅샷을 넘겨도 최신 상태와 다르면 `ConcurrencyConflictError`로 거부된다
(§3.4 `OMS_CONCURRENCY_CONFLICT` — 새 taxonomy 없이 105번 기존 예외 재사용).

`audit_bridge.emit`을 이 어댑터가 직접 호출하는 이유(계층상 adapters →
application 방향이라 이례적) — I-10 "배선·우회불가": `transition()`이 OMS의
유일한 전이 경로가 되면(L4-09 cutover), 감사 기록이 호출부의 기억에 의존하지
않고 이 경로를 쓰는 모든 곳에서 항상 같이 남는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.application import audit_bridge
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import ALLOWED


class OrderNotFoundError(Exception):
    """`order_id`에 해당하는 행이 없다. 다른 tenant 소유라 접근 범위 밖인
    경우와 실제로 존재하지 않는 경우를 이 포트는 구분하지 않는다(§2-C
    `get_for_update`에 tenant 인자가 없다) — 둘 다 호출부 관점에서 "그 id로는
    아무것도 없음"으로 동형(404) 처리한다. tenant 소유권 자체를 확인하는
    책임은 이 어댑터를 호출하는 application 계층에 있다."""


def _row_to_view(row: asyncpg.Record) -> OrderView:
    return OrderView(
        order_id=row["order_id"],
        tenant_id=row["user_id"],
        execution_id=row["execution_id"],
        client_order_id=row["client_order_id"],
        exchange_order_id=row["exchange_order_id"],
        symbol=row["symbol"],
        venue_symbol=row["venue_symbol"],
        exchange=row["exchange"],
        side=OrderSide(row["side"]),
        order_type=OrderType(row["order_type"]),
        time_in_force=row["time_in_force"],
        quantity=row["quantity"],
        price=row["price"],
        status=OrderStatus(row["status"]),
        filled_quantity=row["filled_quantity"],
        average_fill_price=row["average_fill_price"],
        fee_total=row["fee_total"],
        fee_currency=row["fee_currency"],
        version=row["version"],
        parent_order_id=row["parent_order_id"],
        algo_run_id=row["algo_run_id"],
        unknown_since=row["unknown_since"],
        provider_order_date=row["provider_order_date"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresOrderRepository:
    """`OrderRepoPort` 구현. 상태 없음 — 매 호출이 `conn`만으로 동작한다."""

    def __init__(self) -> None:
        self._events = PostgresOrderEventRepository()

    async def get_for_update(self, conn: asyncpg.Connection, order_id: UUID) -> OrderView:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE order_id = $1 FOR UPDATE", order_id
        )
        if row is None:
            raise OrderNotFoundError(str(order_id))
        return _row_to_view(row)

    async def find_by_scope_hash(
        self, conn: asyncpg.Connection, scope_hash: str
    ) -> OrderView | None:
        row = await conn.fetchrow(
            "SELECT o.* FROM orders o JOIN order_idempotency oi ON oi.order_id = o.order_id "
            "WHERE oi.scope_hash = $1",
            scope_hash,
        )
        return None if row is None else _row_to_view(row)

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
    ) -> OrderView:
        current = await self.get_for_update(conn, order_id)
        if current.status != expected_status or current.version != expected_version:
            raise ConcurrencyConflictError(
                f"orders.order_id={order_id}: 실제 {current.status.value}/v{current.version} "
                f"!= 기대 {expected_status.value}/v{expected_version} — 동시 갱신 충돌"
                "(재조회 후 재시도)."
            )
        if new_status not in ALLOWED[current.status]:
            raise InvalidOrderTransitionError(
                f"{current.status.value} -({event.event})-> {new_status.value} 는 §4.2 "
                "전이표에 없는 조합입니다."
            )

        # §5.1 순서 — SET LOCAL 다음 order_events INSERT, 그 다음에야 UPDATE.
        # 073beca589d5의 I6 트리거가 이 순서를 어기면(이벤트 없이 status
        # 변경) RAISE로 거부한다(cutover 무장 후).
        await conn.execute("SELECT set_config('oms.event_written', '1', true)")
        seq = await self._events.append(conn, event)

        set_values: dict[str, Any] = {
            **patch,
            "status": new_status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        row = await conditional_update(
            conn,
            table="orders",
            id_column="order_id",
            id_value=order_id,
            expected_state_column="status",
            expected_state_value=expected_status.value,
            extra_conditions={"version": expected_version},
            set_values=set_values,
            returning="*",
        )

        await audit_bridge.emit(
            conn, event.model_copy(update={"seq": seq}), trace_id=event.trace_id
        )
        return _row_to_view(row)
