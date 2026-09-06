"""L4-26 `order_query` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-26
("application/order_query.py ... 서비스 함수까지 — 선행 07").

tenant 격리 자체(교차 tenant 404 동형)는 `tests/adversarial/oms/
test_cross_tenant_isolation.py`가 별도로 증명한다 — 이 파일은 단일
tenant 관점에서 조회 함수 3개(get_order/list_orders/list_order_events)의
정상 동작·페이지네이션·negative case(유효하지 않은 cursor)만 다룬다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.application import order_query
from src.services.oms.ports.repository import OrderQueryPort
from tests.integration.oms.conftest import create_test_user, insert_order


def test_order_query_module_satisfies_port() -> None:
    """I-10 배선 증명 — module-level 함수 3개가 `OrderQueryPort`를 만족한다."""
    assert isinstance(order_query, OrderQueryPort)


async def test_get_order_returns_view_for_owner(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")

    view = await order_query.get_order(pool, order_id, tenant_id=user_id)

    assert view is not None
    assert view.order_id == order_id
    assert view.tenant_id == user_id
    assert view.status is OrderStatus.CREATED


async def test_get_order_missing_id_returns_none(pool) -> None:
    user_id = await create_test_user(pool)
    view = await order_query.get_order(pool, uuid4(), tenant_id=user_id)
    assert view is None


async def test_list_orders_pages_newest_first_with_keyset_cursor(pool) -> None:
    user_id = await create_test_user(pool)
    base = datetime.now(timezone.utc)
    order_ids = []
    async with pool.acquire() as conn:
        for i in range(3):
            order_ids.append(
                await insert_order(conn, user_id, created_at=base + timedelta(seconds=i))
            )
    newest_first = list(reversed(order_ids))

    page1, cursor1 = await order_query.list_orders(pool, tenant_id=user_id, limit=2)
    assert [v.order_id for v in page1] == newest_first[:2]
    assert cursor1 is not None

    page2, cursor2 = await order_query.list_orders(
        pool, tenant_id=user_id, limit=2, cursor=cursor1
    )
    assert [v.order_id for v in page2] == newest_first[2:]
    assert cursor2 is None


async def _seed_execution_id(conn, user_id) -> int:
    await conn.execute(
        """
        INSERT INTO strategies (
            strategy_id, version, owner_user_id, target_asset, market, exchange,
            fsm_definition, author_agent
        ) VALUES ('oms-query-test', '1.0.0', $1, 'BTC/USDT', 'SPOT', 'bitget', '{}'::jsonb, 'test')
        ON CONFLICT (strategy_id, version) DO NOTHING
        """,
        user_id,
    )
    execution_id: int = await conn.fetchval(
        """
        INSERT INTO strategy_executions (
            strategy_id, strategy_version, user_id, exchange, mode, allocated_capital
        ) VALUES ('oms-query-test', '1.0.0', $1, 'bitget', 'PAPER', 1000)
        RETURNING id
        """,
        user_id,
    )
    return execution_id


async def test_list_orders_filters_by_execution_id(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        execution_id = await _seed_execution_id(conn, user_id)
        await insert_order(conn, user_id)  # execution_id NULL — 필터에 안 걸려야 함
        target = await insert_order(conn, user_id)
        await conn.execute(
            "UPDATE orders SET execution_id = $1 WHERE order_id = $2", execution_id, target
        )

    items, _ = await order_query.list_orders(pool, tenant_id=user_id, execution_id=execution_id)

    assert [v.order_id for v in items] == [target]


async def test_list_orders_filters_by_statuses(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        created = await insert_order(conn, user_id, status="CREATED")
        await insert_order(conn, user_id, status="FILLED")

    items, _ = await order_query.list_orders(
        pool, tenant_id=user_id, statuses=[OrderStatus.CREATED]
    )

    assert [v.order_id for v in items] == [created]


async def test_list_orders_invalid_cursor_raises(pool) -> None:
    """negative — 변조된 cursor는 최선 추측으로 넘어가지 않고 거부한다."""
    user_id = await create_test_user(pool)
    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(pool, tenant_id=user_id, cursor="not-a-valid-cursor")


async def test_list_order_events_returns_timeline_in_seq_order(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        await conn.execute(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, event, actor_subject_id, trace_id,
                occurred_at, payload_hash
            ) VALUES
                ($1, 'CREATED', 'VALIDATED', 'VALIDATED', 'system', $2, now(), $3),
                ($1, 'VALIDATED', 'SUBMITTED', 'SENT', 'system', $2, now(), $3)
            """,
            order_id,
            uuid4(),
            "e" * 64,
        )

    events = await order_query.list_order_events(pool, order_id, tenant_id=user_id)

    assert events is not None
    assert [e.event for e in events] == ["VALIDATED", "SENT"]


async def test_list_order_events_missing_order_returns_none(pool) -> None:
    user_id = await create_test_user(pool)
    events = await order_query.list_order_events(pool, uuid4(), tenant_id=user_id)
    assert events is None
