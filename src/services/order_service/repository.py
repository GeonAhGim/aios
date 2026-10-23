"""FD-4.2-c/FD-4.5 — orders 테이블 영속화(05번 §5.6 "DB가 항상 진실의 원천").

Order 모델(01번)과 orders 테이블(04번) 사이의 매핑만 담당 — 판단·전송
로직은 여기 없다(submit.py/cancel.py/modify.py/reconcile.py가 호출).

편차: `price`/`average_fill_price` 컬럼은 NUMERIC뿐(통화 컬럼 없음, 기존
스키마 결정) — Phase 1 크립토 실행은 전량 Currency.USDT이므로 이 계층이
그 가정으로 Money를 재구성한다(다자산군 확장 시 통화 컬럼 추가 필요,
이 leaf 스콥 밖).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent
from src.services.oms.domain.state_machine import OrderEvent as OmsOrderEvent

_order_events = PostgresOrderEventRepository()
_fills = FillsRepository()


def _row_to_order(row: asyncpg.Record) -> Order:
    price = Money(amount=row["price"], currency=Currency.USDT) if row["price"] is not None else None
    average_fill_price = (
        Money(amount=row["average_fill_price"], currency=Currency.USDT)
        if row["average_fill_price"] is not None
        else None
    )
    asset_class = (
        AssetClass(row["asset_class"]) if row["asset_class"] is not None else AssetClass.CRYPTO
    )
    return Order(
        order_id=row["order_id"],
        exchange_order_id=row["exchange_order_id"],
        client_order_id=row["client_order_id"],
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        execution_id=row["execution_id"],
        symbol=row["symbol"],
        exchange=row["exchange"],
        side=OrderSide(row["side"]),
        order_type=OrderType(row["order_type"]),
        quantity=row["quantity"],
        price=price,
        status=OrderStatus(row["status"]),
        filled_quantity=row["filled_quantity"],
        average_fill_price=average_fill_price,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_liquidation=row["is_liquidation"],
        asset_class=asset_class,
    )


async def get_by_client_order_id(conn: asyncpg.Connection, client_order_id: str) -> Order | None:
    row = await conn.fetchrow("SELECT * FROM orders WHERE client_order_id = $1", client_order_id)
    return None if row is None else _row_to_order(row)


async def get_by_order_id(conn: asyncpg.Connection, order_id: UUID) -> Order | None:
    row = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", order_id)
    return None if row is None else _row_to_order(row)


async def insert(
    conn: asyncpg.Connection,
    order: Order,
    *,
    user_id: UUID,
    risk_decision_id: UUID | None = None,
) -> Order:
    """R-37 — `risk_decision_id`는 `93c0e7f6b8d9` 트리거가 검증하는 I1
    참조다(같은 tenant·ALLOW/REDUCE·만료 전). None 허용은 cutover 비무장
    기간의 legacy 호출부(`submit.py`)를 위한 것이고, 무장 뒤에는 DB가
    NULL INSERT를 거부한다(코드가 아니라 트리거가 최종 권위)."""
    row = await conn.fetchrow(
        """
        INSERT INTO orders (
            order_id, user_id, client_order_id, exchange_order_id, strategy_id,
            strategy_version, execution_id, symbol, exchange, side, order_type,
            quantity, price, status, filled_quantity, average_fill_price,
            is_liquidation, asset_class, risk_decision_id
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
        RETURNING *
        """,
        order.order_id,
        user_id,
        order.client_order_id,
        order.exchange_order_id,
        order.strategy_id,
        order.strategy_version,
        order.execution_id,
        order.symbol,
        order.exchange,
        order.side.value,
        order.order_type.value,
        order.quantity,
        order.price.amount if order.price is not None else None,
        order.status.value,
        order.filled_quantity,
        order.average_fill_price.amount if order.average_fill_price is not None else None,
        order.is_liquidation,
        order.asset_class.value,
        risk_decision_id,
    )
    return _row_to_order(row)


async def update_from_exchange(
    conn: asyncpg.Connection, order: Order, *, expected_status: OrderStatus
) -> Order:
    """거래소 응답 반영 후(FD-4.2-b/FD-3.4) 상태를 갱신한다. `order_id`로
    대상 행을 특정한다(client_order_id는 UNIQUE라 order_id 대신 써도
    무방하지만, order_id가 이 시스템의 정식 기본키다).

    레드팀 #2026-09-02-20 — 이 행을 마지막으로 읽었을 때의 status
    (`expected_status`)와 실제로 쓰는 시점의 status가 다르면(동시에 다른
    경로가 먼저 갱신) `ConcurrencyConflictError`를 던진다 — 105번 표준의
    `conditional_update()`를 그대로 쓴다. 호출자는 항상 자신이 읽은
    order의 갱신 *이전* status를 넘겨야 한다(tick.py/cancel.py/
    reconcile.py 모두 갱신 직전에 fresh read한 order를 그대로 쓴다)."""
    row = await conditional_update(
        conn,
        table="orders",
        id_column="order_id",
        id_value=order.order_id,
        expected_state_column="status",
        expected_state_value=expected_status.value,
        set_values={
            "exchange_order_id": order.exchange_order_id,
            "status": order.status.value,
            "filled_quantity": order.filled_quantity,
            "average_fill_price": (
                order.average_fill_price.amount if order.average_fill_price is not None else None
            ),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    return _row_to_order(row)


_SYNC_RESPONSE_EVENTS: dict[OrderStatus, OmsOrderEvent] = {
    OrderStatus.FILLED: OmsOrderEvent.FILL,
    OrderStatus.PARTIALLY_FILLED: OmsOrderEvent.FILL,
    OrderStatus.REJECTED: OmsOrderEvent.VENUE_REJECTED,
    OrderStatus.SUBMITTED: OmsOrderEvent.ACK,
    OrderStatus.ACKNOWLEDGED: OmsOrderEvent.ACK,
    OrderStatus.CANCELLED: OmsOrderEvent.VENUE_CANCELLED,
    OrderStatus.EXPIRED: OmsOrderEvent.VENUE_EXPIRED,
    OrderStatus.UNKNOWN: OmsOrderEvent.RESPONSE_LOST,
}


def _sync_event_kind(status: OrderStatus) -> OmsOrderEvent:
    """Event kind recorded for a synchronous place_order response; fail-closed
    on a status with no mapped event so no eventless transition is persisted."""
    try:
        return _SYNC_RESPONSE_EVENTS[status]
    except KeyError:
        raise ValueError(
            f"no order event mapped for synchronous response status {status.value}"
        ) from None


async def update_from_exchange_with_fill_event(
    conn: asyncpg.Connection, order: Order, *, expected_status: OrderStatus, actor_subject_id: UUID
) -> Order:
    """task-16630/16657 — `update_from_exchange`의 동기 응답 변형(FILLED뿐 아니라
    REJECTED/SUBMITTED 등 모든 동기 상태에 order_events를 남긴다). 거래소가
    `place_order` 응답에서 즉시 FILLED를 돌려주는 legacy 경로(`submit.py`
    FD-4.2-c)는 `CREATED`에서 바로 `FILLED`로 넘어가는데, 이 쌍은
    `state_machine.ALLOWED[CREATED]`에 없다(그 모듈 docstring 참조 — 이
    legacy claim 경로는 `oms.application.inbox_processor.InboxProcessor`/
    `order_repository.transition()`을 거치지 않고 애초에 상태기계 검증
    밖에 있었다). 그래서 `transition()`을 재사용하지 않고, 이 companion
    함수가 같은 보장(상태 갱신과 `order_events`/`fills` 기록이 한 커밋)만
    별도로 만든다 — `replay_verify`가 이 경로로 체결된 주문도 이벤트
    이력으로 폴드해 실제 상태(FILLED)에 도달하게 하기 위함(sentinel: orders
    residue).

    쓰기 순서(105번 §5.6 + L4-06 I6 정신을 legacy 경로에도 적용): `SET LOCAL
    oms.event_written`으로 I6 트리거가 바로 뒤의 상태 UPDATE를 통과시키게
    한 뒤 → `order_events` INSERT → (체결량 > 0이면) `fills` INSERT → 마지막에
    `conditional_update`로 `orders` UPDATE. 전부 호출자가 이미 연 트랜잭션
    안에서 실행되므로(아래 `conn.transaction()`), 셋 중 하나라도 실패하면
    전체가 롤백돼 "FILLED로 저장됐지만 이벤트/체결 기록이 없는" 상태가
    생기지 않는다."""
    now = datetime.now(timezone.utc)
    provider_id = f"sync:{order.order_id}:{order.filled_quantity}"
    payload_hash = hashlib.sha256(
        f"{order.order_id}:{expected_status.value}:{order.status.value}:"
        f"{order.filled_quantity}".encode()
    ).hexdigest()
    event = OrderTransitionEvent(
        order_id=order.order_id,
        from_status=expected_status,
        to_status=order.status,
        event=_sync_event_kind(order.status).value,
        reason_code="SYNC_FILL" if order.filled_quantity > 0 else "SYNC_RESPONSE",
        actor_subject_id=actor_subject_id,
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=provider_id,
        occurred_at=now,
        payload_hash=payload_hash,
    )
    fill_recorded = order.filled_quantity > 0 and order.average_fill_price is not None
    async with conn.transaction():
        await conn.execute("SELECT set_config('oms.event_written', '1', true)")
        await _order_events.append(conn, event)
        if fill_recorded and order.average_fill_price is not None:
            if order.exchange_order_id is None:
                raise ValueError(
                    f"order_id={order.order_id}: FILLED 응답인데 exchange_order_id가 "
                    "없습니다 — fills 기록을 위한 필수 필드(fail-closed, 추측 금지)."
                )
            fill = FillEvent(
                provider_fill_id=provider_id,
                venue=order.exchange,
                order_id=order.order_id,
                exchange_order_id=order.exchange_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.filled_quantity,
                price=order.average_fill_price.amount,
                fee=Decimal("0"),
                fee_currency=order.average_fill_price.currency.value,
                liquidity="UNKNOWN",
                venue_ts=now,
            )
            await _fills.insert_if_absent(conn, fill)
        row = await conditional_update(
            conn,
            table="orders",
            id_column="order_id",
            id_value=order.order_id,
            expected_state_column="status",
            expected_state_value=expected_status.value,
            set_values={
                "exchange_order_id": order.exchange_order_id,
                "status": order.status.value,
                "filled_quantity": order.filled_quantity,
                "average_fill_price": (
                    order.average_fill_price.amount
                    if order.average_fill_price is not None
                    else None
                ),
                "updated_at": now,
                # The projection folds fills into fee_total/fee_currency, so the
                # row must carry them too or replay_verify flags a mismatch.
                **(
                    {
                        "fee_total": Decimal("0"),
                        "fee_currency": order.average_fill_price.currency.value,
                    }
                    if fill_recorded and order.average_fill_price is not None
                    else {}
                ),
            },
        )
    return _row_to_order(row)


async def fail_claim_with_event(
    conn: asyncpg.Connection, order_id: UUID, *, reason_code: str, actor_subject_id: UUID
) -> None:
    """task-16657 — CREATED->FAILED for a claim row that was never sent (e.g. a
    stale risk fence), with its `order_events` row in the same transaction so
    the row never becomes eventless residue. Same write order as
    `update_from_exchange_with_fill_event` (event_written flag, event, UPDATE)."""
    now = datetime.now(timezone.utc)
    event = OrderTransitionEvent(
        order_id=order_id,
        from_status=OrderStatus.CREATED,
        to_status=OrderStatus.FAILED,
        event=OmsOrderEvent.VALIDATION_FAILED.value,
        reason_code=reason_code,
        actor_subject_id=actor_subject_id,
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=now,
        payload_hash=hashlib.sha256(f"{order_id}:{reason_code}".encode()).hexdigest(),
    )
    async with conn.transaction():
        await conn.execute("SELECT set_config('oms.event_written', '1', true)")
        await _order_events.append(conn, event)
        await conditional_update(
            conn,
            table="orders",
            id_column="order_id",
            id_value=order_id,
            expected_state_column="status",
            expected_state_value=OrderStatus.CREATED.value,
            set_values={"status": OrderStatus.FAILED.value, "updated_at": now},
        )


async def update_after_modify(
    conn: asyncpg.Connection, order: Order, *, expected_status: OrderStatus
) -> Order:
    """FD-4.4 전용 — 정정은 status/체결정보뿐 아니라 price/quantity 자체가
    바뀌므로 update_from_exchange와 별도 UPDATE 문이 필요하다. 동시성
    방어 원칙은 update_from_exchange와 동일(#2026-09-02-20)."""
    row = await conditional_update(
        conn,
        table="orders",
        id_column="order_id",
        id_value=order.order_id,
        expected_state_column="status",
        expected_state_value=expected_status.value,
        set_values={
            "price": order.price.amount if order.price is not None else None,
            "quantity": order.quantity,
            "status": order.status.value,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    return _row_to_order(row)


async def count_recent_trades(
    conn: asyncpg.Connection, execution_id: int, *, since_hours: Decimal
) -> int:
    """FD-8.3 Trade Frequency 지표용 — 이 실행의 최근 N시간 주문 건수."""
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM orders WHERE execution_id = $1 "
        "AND created_at >= now() - ($2 || ' hours')::interval",
        execution_id,
        str(since_hours),
    )
    return int(count)
