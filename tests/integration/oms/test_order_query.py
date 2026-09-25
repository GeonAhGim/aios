"""L4-26 `order_query` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-26
("application/order_query.py ... 서비스 함수까지 — 선행 07").

tenant 격리 자체(교차 tenant 404 동형)는 `tests/adversarial/oms/
test_cross_tenant_isolation.py`가 별도로 증명한다 — 이 파일은 단일
tenant 관점에서 조회 함수 3개(get_order/list_orders/list_order_events)의
정상 동작·페이지네이션·negative case(유효하지 않은 cursor)만 다룬다.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.application import order_query
from src.services.oms.ports.repository import OrderQueryPort
from tests.integration.oms.conftest import create_test_user, insert_order


def _raw_cursor(created_at_str: str, order_id: str) -> str:
    """Build a cursor with an arbitrary (possibly malformed) timestamp string,
    bypassing `_encode_cursor`'s always-aware `datetime` — mirrors how an
    external caller could craft one."""
    raw = f"{created_at_str}|{order_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


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

    page2, cursor2 = await order_query.list_orders(pool, tenant_id=user_id, limit=2, cursor=cursor1)
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


async def test_list_orders_cursor_with_valid_alphabet_leading_garbage_raises(pool) -> None:
    """negative — task-4964: `!!!` 같은 알파벳 밖 문자가 아니라, base64
    알파벳 안에 속하는 쓰레기 블록(`AAAA`)을 앞에 붙여도 거부해야 한다.
    `!!!garbage!!!` 케이스는 `binascii.Error`(알파벳 검증)만으로 걸러져
    strict 디코드 이후의 partition/fromisoformat/UUID 파싱 경로를 실제로
    거치지 않는다 — 이 케이스가 그 경로를 검증한다."""
    user_id = await create_test_user(pool)
    valid_cursor = order_query._encode_cursor(datetime.now(timezone.utc), uuid4())
    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(pool, tenant_id=user_id, cursor="AAAA" + valid_cursor)


async def test_list_orders_cursor_with_valid_alphabet_trailing_garbage_raises(pool) -> None:
    """negative — task-4964: 위와 동일하되 뒤에 붙는 경우."""
    user_id = await create_test_user(pool)
    valid_cursor = order_query._encode_cursor(datetime.now(timezone.utc), uuid4())
    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(pool, tenant_id=user_id, cursor=valid_cursor + "AAAA")


async def test_list_orders_cursor_with_leading_garbage_raises(pool) -> None:
    """negative — task-4897 REJECT: 앞에 쓰레기 문자가 섞인 커서를
    lenient urlsafe_b64decode가 관대하게 무시하고 정상 페이지를 반환하면
    안 된다(fail-closed 계약 위반)."""
    user_id = await create_test_user(pool)
    valid_cursor = order_query._encode_cursor(datetime.now(timezone.utc), uuid4())
    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(
            pool, tenant_id=user_id, cursor="!!!garbage!!!" + valid_cursor
        )


async def test_list_orders_cursor_with_trailing_garbage_raises(pool) -> None:
    """negative — task-4897 REJECT: 뒤에 쓰레기 문자가 섞인 커서도 동일하게
    거부되어야 한다."""
    user_id = await create_test_user(pool)
    valid_cursor = order_query._encode_cursor(datetime.now(timezone.utc), uuid4())
    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(
            pool, tenant_id=user_id, cursor=valid_cursor + "!!!garbage!!!"
        )


async def test_list_orders_naive_cursor_timestamp_rejected(pool) -> None:
    """negative — task-4901 REJECT: timezone 없는 cursor timestamp는
    asyncpg가 서버 로컬시간대로 암묵 해석할 수 있어 fail-closed로 거부해야
    한다."""
    user_id = await create_test_user(pool)
    naive_cursor = _raw_cursor("2026-01-01T00:00:00", str(uuid4()))

    with pytest.raises(order_query.InvalidOrderCursorError):
        await order_query.list_orders(pool, tenant_id=user_id, cursor=naive_cursor)


async def test_list_orders_non_utc_offset_cursor_paginates_correctly(pool) -> None:
    """negative-adjacent — UTC가 아닌 offset(+09:00)이 붙은 cursor는
    거부되지 않고, 정보 손실 없이 올바른 순서로 keyset 비교된다."""
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
    assert cursor1 is not None
    cursor_created_at, cursor_order_id = order_query._decode_cursor(cursor1)
    kst = timezone(timedelta(hours=9))
    kst_cursor = _raw_cursor(cursor_created_at.astimezone(kst).isoformat(), str(cursor_order_id))

    page2, cursor2 = await order_query.list_orders(
        pool, tenant_id=user_id, limit=2, cursor=kst_cursor
    )

    assert [v.order_id for v in page2] == newest_first[2:]
    assert cursor2 is None


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
