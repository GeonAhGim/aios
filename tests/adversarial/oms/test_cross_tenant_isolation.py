"""L4-26 DoD — `order_query` 교차 tenant 격리.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-26,
§8.3(존재 자체 비노출·"404 동형") — LA-22(task-825, 190dfea)
`BatchRepository.get()` tenant_id 필터와 동일한 결함 클래스를 겨냥한다.

tenant A(owner) 소유 주문을 tenant B(attacker)가 `order_id`만 알고
조회를 시도할 때: (1) 단건 조회는 "존재하지 않음"과 동형으로 `None`을
반환하고(존재 여부 자체를 노출하지 않음), (2) 목록 조회에는 A의 행이
0건이며, (3) 이벤트 이력 조회도 동형 `None`이다. `orders`는 아직
RLS가 없다(073beca589d5 note — `_LEGACY_TABLES_POLICY_ONLY`) — 이
테스트가 실DB로 단언하는 tenant_id 필터가 지금은 **유일한** 방어선이고,
PLT-30이 나중에 RLS를 켜면 이중 방어가 된다.
"""
from __future__ import annotations

from uuid import uuid4

from src.services.oms.application import order_query
from tests.integration.oms.conftest import create_test_user, insert_order


async def test_get_order_cross_tenant_does_not_leak_existence(pool) -> None:
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, owner_id)

    owner_view = await order_query.get_order(pool, order_id, tenant_id=owner_id)
    attacker_view = await order_query.get_order(pool, order_id, tenant_id=attacker_id)
    missing_view = await order_query.get_order(pool, uuid4(), tenant_id=attacker_id)

    assert owner_view is not None and owner_view.tenant_id == owner_id, (
        "전제(주문이 존재하고 owner 소유)가 재현되지 않았습니다"
    )
    assert attacker_view is None, (
        "get_order()가 tenant를 구분하지 않아 attacker_id로도 owner의 주문이 "
        "조회됩니다(§8.3 '404 동형' 위반)"
    )
    assert attacker_view == missing_view, (
        "타 tenant 소유 주문과 존재하지 않는 주문이 같은 None으로 동형이어야 합니다"
        "(§8.3 '존재 자체 비노출')"
    )


async def test_list_orders_excludes_other_tenant_rows(pool) -> None:
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await insert_order(conn, owner_id)
        await insert_order(conn, owner_id)
        own_order_id = await insert_order(conn, attacker_id)

    items, _ = await order_query.list_orders(pool, tenant_id=attacker_id)

    assert [v.order_id for v in items] == [own_order_id], (
        "list_orders()가 tenant_id로 필터되지 않아 다른 tenant의 주문이 "
        "목록에 섞여 나옵니다"
    )


async def test_list_orders_for_tenant_with_no_orders_is_empty(pool) -> None:
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await insert_order(conn, owner_id)

    items, cursor = await order_query.list_orders(pool, tenant_id=attacker_id)

    assert items == []
    assert cursor is None


async def test_list_order_events_cross_tenant_does_not_leak_existence(pool) -> None:
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, owner_id)
        await conn.execute(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, event, actor_subject_id, trace_id,
                occurred_at, payload_hash
            ) VALUES ($1, 'CREATED', 'VALIDATED', 'VALIDATED', 'system', $2, now(), $3)
            """,
            order_id,
            uuid4(),
            "e" * 64,
        )

    owner_events = await order_query.list_order_events(pool, order_id, tenant_id=owner_id)
    attacker_events = await order_query.list_order_events(pool, order_id, tenant_id=attacker_id)
    missing_events = await order_query.list_order_events(pool, uuid4(), tenant_id=attacker_id)

    assert owner_events is not None and len(owner_events) == 1, (
        "전제(이벤트가 존재하고 owner 소유 주문에 달림)가 재현되지 않았습니다"
    )
    assert attacker_events is None, (
        "list_order_events()가 tenant를 구분하지 않아 attacker_id로도 owner의 "
        "이벤트 이력이 조회됩니다(§8.3 '404 동형' 위반)"
    )
    assert attacker_events == missing_events, (
        "타 tenant 소유 주문의 이벤트 이력과 존재하지 않는 주문의 이벤트 이력이 "
        "같은 None으로 동형이어야 합니다"
    )
