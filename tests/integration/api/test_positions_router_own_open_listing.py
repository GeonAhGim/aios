"""LB-19 / FA-6: position listing — own open positions only."""

from __future__ import annotations

from decimal import Decimal

from tests.support.positions_router import BASE, _create_account, _open_position, _register
from tests.support.positions_router import client as client  # noqa: F401
from tests.support.positions_router import pool as pool  # noqa: F401


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
