"""LB-19: NAV date ranges and tenant isolation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.contracts.v1 import (
    NAVSnapshot,
)
from tests.support.positions_router import (
    BASE,
    _assert_error_envelope,
    _create_account,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


async def _insert_nav(pool: asyncpg.Pool, account_id: UUID, day: date, cash: Decimal) -> None:
    nav = NAVSnapshot(
        account_id=account_id,
        nav_date=day,
        base_currency=Currency.KRW,
        opening_nav=cash,
        cash=cash,
        positions_mv=Decimal("0"),
        realized=Decimal("0"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=cash,
        fx_rates=[],
        source_hash="ab" * 32,
    )
    async with pool.acquire() as conn:
        await PostgresNavRepository(pool).insert(conn, nav)



def _nav_params(account_id: UUID, start: str, end: str) -> dict[str, str]:
    return {"account_id": str(account_id), "start_date": start, "end_date": end}



async def test_nav_series_is_ascending_and_exposes_missing_days(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _insert_nav(pool, account_id, date(2026, 9, 3), Decimal("1000"))
    await _insert_nav(pool, account_id, date(2026, 9, 1), Decimal("900"))

    response = await client.get(
        f"{BASE}/nav",
        headers=headers,
        params=_nav_params(account_id, "2026-09-01", "2026-09-03"),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["nav_date"] for item in data["items"]] == ["2026-09-01", "2026-09-03"]
    assert Decimal(data["items"][0]["closing_nav"]) == Decimal("900")
    assert data["missing_dates"] == ["2026-09-02"]



async def test_nav_cross_tenant_is_404_and_bad_range_is_rejected(client, pool):
    _, victim_id = await _register(client)
    attacker_headers, attacker_id = await _register(client)
    victim_account = await _create_account(pool, victim_id)
    await _insert_nav(pool, victim_account, date(2026, 9, 1), Decimal("1"))
    own_account = await _create_account(pool, attacker_id)

    cross = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(victim_account, "2026-09-01", "2026-09-01"),
    )
    assert cross.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")

    reversed_range = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(own_account, "2026-09-02", "2026-09-01"),
    )
    assert reversed_range.status_code == 400
    _assert_error_envelope(reversed_range.json(), "VALIDATION_INVALID_FIELD")

    too_long = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(own_account, "2025-01-01", "2026-09-01"),
    )
    assert too_long.status_code == 400
