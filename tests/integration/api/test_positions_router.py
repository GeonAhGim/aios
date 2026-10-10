"""LB-19 / FA-6: position listing and tenant/portfolio scope."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from uuid import UUID

from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.support.positions_router import (
    BASE,
    _assert_error_envelope,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


async def test_positions_require_authentication(client):
    response = await client.get(BASE)
    assert response.status_code == 401



async def test_list_positions_returns_own_open_positions_and_filters(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("2")
    )
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("0"))

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"data", "meta"}
    keys = {item["position_key"] for item in body["data"]["items"]}
    assert len(keys) == 2 and opened.position_key in keys

    filtered = await client.get(
        BASE,
        headers=headers,
        params={"account_id": str(account_id), "instrument_id": str(opened.instrument_id)},
    )
    items = filtered.json()["data"]["items"]
    assert [item["position_key"] for item in items] == [opened.position_key]
    # Decimal은 문자열로 온다(NUMERIC(30,10) 스케일 그대로) — Number 변환 금지(§3.4)
    assert isinstance(items[0]["quantity"], str)
    assert Decimal(items[0]["quantity"]) == Decimal("2")
    assert items[0]["unrealized_pnl_base"] is None  # 마크 없으면 0이 아니라 null
    assert items[0]["schema_version"] == "v1"



async def test_list_positions_other_tenant_account_is_404_isomorphic(client, pool):
    victim_headers, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id)
    await _open_position(pool, tenant_id=victim_id, account_id=account_id, quantity=Decimal("1"))

    cross = await client.get(BASE, headers=attacker_headers, params={"account_id": str(account_id)})
    ghost = await client.get(
        BASE, headers=attacker_headers, params={"account_id": str(uuid.uuid4())}
    )
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert set(cross.json()) == set(ghost.json())

    own = await client.get(BASE, headers=victim_headers, params={"account_id": str(account_id)})
    assert own.status_code == 200 and len(own.json()["data"]["items"]) == 1



async def test_list_positions_without_portfolio_id_is_unchanged_regression(client, pool):
    """FA-6 DoD "기존 단일계좌 응답 무변경" — `portfolio_id`를 주지 않은
    요청은 이 리프 이전과 바이트 동일한 응답을 낸다(새 필드가 몰래 끼어들지
    않는다, 필터링도 걸리지 않는다)."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("2")
    )

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["position_key"] for item in items] == [opened.position_key]
    assert set(items[0]) == {
        "position_key",
        "tenant_id",
        "account_id",
        "instrument_id",
        "quantity",
        "avg_cost",
        "cost_method",
        "lots",
        "realized_pnl_base",
        "unrealized_pnl_base",
        "fees_base",
        "funding_base",
        "mark_price",
        "mark_at",
        "base_currency",
        "last_journal_seq",
        "updated_at",
        "schema_version",
    }



async def test_list_positions_portfolio_id_filters_to_that_portfolio_only(client, pool):
    headers, tenant_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    hierarchy_a = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    hierarchy_b = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    account_id = await _create_account(pool, tenant_id)
    in_a = await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy_a.portfolio.portfolio_id,
    )
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy_b.portfolio.portfolio_id,
    )

    response = await client.get(
        BASE, headers=headers, params={"portfolio_id": str(hierarchy_a.portfolio.portfolio_id)}
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["position_key"] for item in items] == [in_a.position_key]



async def test_list_positions_portfolio_id_rejects_cross_tenant_scope_fail_closed(client, pool):
    """negative — 다른 테넌트 소유 portfolio_id를 주면 그 tenant의 포지션
    전체를 돌려주는 대신(전체 반환 폴백 금지) 404로 거부한다."""
    _, victim_id = await _register(client)
    attacker_headers, attacker_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    victim_hierarchy = await build_hierarchy(pool, repo, tenant_id=victim_id)
    attacker_account = await _create_account(pool, attacker_id)
    await _open_position(
        pool, tenant_id=attacker_id, account_id=attacker_account, quantity=Decimal("1")
    )

    response = await client.get(
        BASE,
        headers=attacker_headers,
        params={"portfolio_id": str(victim_hierarchy.portfolio.portfolio_id)},
    )
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")



async def test_list_positions_portfolio_id_rejects_unknown_portfolio(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    response = await client.get(BASE, headers=headers, params={"portfolio_id": str(uuid.uuid4())})
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")



async def test_list_positions_closed_portfolio_scope_is_rejected_fail_closed(client, pool):
    """게이트 적색 재현 -- portfolio_id는 실존하고 이 tenant 소유지만 폐쇄
    (`closed_at` NOT NULL)된 실제 DB row다(목이 아니다). `resolve_portfolio_scope`의
    `_require_open` 검사가 배선에서 빠지면 이 테스트는 200과 함께 폐쇄
    포트폴리오의 포지션을 그대로 흘려 적색이 된다 -- 존재+소유 확인만으로는
    부족하고 개방 상태까지 fail-closed로 확인해야 함을 실 DB 상태로 증명한다."""
    headers, tenant_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy.portfolio.portfolio_id,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE portfolio SET closed_at = now() WHERE portfolio_id = $1",
            hierarchy.portfolio.portfolio_id,
        )

    response = await client.get(
        BASE, headers=headers, params={"portfolio_id": str(hierarchy.portfolio.portfolio_id)}
    )
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")



async def test_list_positions_portfolio_id_concurrent_mixed_tenants_do_not_cross_leak(client, pool):
    """D3증거 -- 서로 다른 tenant가 `portfolio_id`로 스코프한 `GET /positions`를
    asyncio.gather로 동시에 섞어 호출해도(공유 커넥션 풀·앱 인스턴스) 각
    요청은 자신의 tenant_id/portfolio_id 기준으로만 결과를 받는다 -- 동시
    실행이 만드는 경합으로 한 tenant의 포지션이 다른 tenant 응답에 섞여
    드는 사고(교차 유출)가 없음을 증명한다."""
    repo = PostgresEntityRepository(pool)
    sessions: list[tuple[dict, UUID]] = []
    for _ in range(3):
        headers, tenant_id = await _register(client)
        hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
        account_id = await _create_account(pool, tenant_id)
        await _open_position(
            pool,
            tenant_id=tenant_id,
            account_id=account_id,
            quantity=Decimal("1"),
            portfolio_id=hierarchy.portfolio.portfolio_id,
        )
        sessions.append((headers, hierarchy.portfolio.portfolio_id))

    async def _fetch(headers: dict, portfolio_id: UUID):
        return await client.get(BASE, headers=headers, params={"portfolio_id": str(portfolio_id)})

    calls = [_fetch(headers, portfolio_id) for headers, portfolio_id in sessions for _ in range(3)]
    responses = await asyncio.gather(*calls)

    for response in responses:
        assert response.status_code == 200
        assert len(response.json()["data"]["items"]) == 1
