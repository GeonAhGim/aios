"""LB-19: journal pagination and tenant isolation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
)
from tests.support.positions_router import (
    BASE,
    _assert_error_envelope,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


async def _append_fills(pool: asyncpg.Pool, position_key: str, count: int) -> None:
    repo = PostgresJournalRepository(pool)
    for i in range(count):
        async with pool.acquire() as conn, conn.transaction():
            await repo.append(
                conn,
                position_key=position_key,
                entry_type=JournalEntryType.FILL,
                qty_delta=Decimal("1"),
                price=Money(amount=Decimal("100"), currency=Currency.KRW),
                fee=None,
                realized_pnl_base=Decimal("0"),
                fx_rate=None,
                fx_source=None,
                source_event_type="fill",
                source_event_id=f"{position_key}:{i}",
                idempotency_key=f"fill:{position_key}:{i}",
                occurred_at=datetime.now(timezone.utc),
            )



async def test_journal_cursor_pagination_walks_in_sequence_order(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )
    await _append_fills(pool, opened.position_key, 3)

    first = await client.get(
        f"{BASE}/{opened.position_key}/journal", headers=headers, params={"limit": 2}
    )
    assert first.status_code == 200
    body = first.json()
    assert [e["sequence_no"] for e in body["data"]["items"]] == [1, 2]
    assert body["meta"]["page"]["next_cursor"] == "2"
    assert body["data"]["items"][0]["entry_type"] == "FILL"
    assert body["data"]["items"][0]["prev_hash"] is None

    second = await client.get(
        f"{BASE}/{opened.position_key}/journal",
        headers=headers,
        params={"limit": 2, "cursor": body["meta"]["page"]["next_cursor"]},
    )
    body2 = second.json()
    assert [e["sequence_no"] for e in body2["data"]["items"]] == [3]
    assert body2["meta"]["page"]["next_cursor"] is None



async def test_journal_cross_tenant_is_404_isomorphic_with_unknown_key(client, pool):
    _, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id)
    opened = await _open_position(
        pool, tenant_id=victim_id, account_id=account_id, quantity=Decimal("1")
    )
    await _append_fills(pool, opened.position_key, 1)

    cross = await client.get(f"{BASE}/{opened.position_key}/journal", headers=attacker_headers)
    ghost = await client.get(f"{BASE}/TESTVENUE:nope:strat:exec/journal", headers=attacker_headers)
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert cross.json()["error_code"] == ghost.json()["error_code"]



async def test_journal_rejects_malformed_cursor(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )

    response = await client.get(
        f"{BASE}/{opened.position_key}/journal", headers=headers, params={"cursor": "abc"}
    )
    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")
