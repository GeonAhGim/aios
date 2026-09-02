"""FD-4.2-c/FD-4.5 — orders 테이블 영속화(05번 §5.6 "DB가 항상 진실의 원천").

Order 모델(01번)과 orders 테이블(04번) 사이의 매핑만 담당 — 판단·전송
로직은 여기 없다(submit.py/cancel.py/modify.py/reconcile.py가 호출).

편차: `price`/`average_fill_price` 컬럼은 NUMERIC뿐(통화 컬럼 없음, 기존
스키마 결정) — Phase 1 크립토 실행은 전량 Currency.USDT이므로 이 계층이
그 가정으로 Money를 재구성한다(다자산군 확장 시 통화 컬럼 추가 필요,
이 leaf 스콥 밖).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType


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


async def insert(conn: asyncpg.Connection, order: Order, *, user_id: UUID) -> Order:
    row = await conn.fetchrow(
        """
        INSERT INTO orders (
            order_id, user_id, client_order_id, exchange_order_id, strategy_id,
            strategy_version, execution_id, symbol, exchange, side, order_type,
            quantity, price, status, filled_quantity, average_fill_price,
            is_liquidation, asset_class
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
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


async def delete(conn: asyncpg.Connection, order_id: UUID) -> None:
    """#2026-09-02-19 — claim-then-send 순서에서 거래소 전송 자체가 실패했을
    때 claim 행을 정리한다. "전송 실패는 DB에 아무 흔적도 남기지 않는다"는
    기존 불변조건(test_submit_order_network_error_propagates)을 유지한다."""
    await conn.execute("DELETE FROM orders WHERE order_id = $1", order_id)


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
