"""FA-6: regression — list without portfolio_id returns unchanged response shape."""

from __future__ import annotations

from decimal import Decimal

from tests.support.positions_router import (
    BASE,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client  # noqa: F401
from tests.support.positions_router import pool as pool  # noqa: F401


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
