"""task-11513: FA-3 backfills must not change another module's orders."""
import os

import asyncpg
import pytest

from tests.conftest import _TEST_DATABASE_URL
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities import test_migration_fa3_orders_fills_columns as source
from tests.integration.foundation.entities.test_migration_fa3_orders_fills_columns import (
    _ensure_head as _ensure_head,  # noqa: F401 -- autouse isolation fixture
)
from tests.integration.foundation.entities.test_migration_fa3_orders_fills_columns import (
    pool as pool,  # noqa: F401 -- pool follows the isolated DATABASE_URL
)


async def test_backfill_does_not_increment_shared_order_version(pool):
    shared = await asyncpg.create_pool(
        _TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1, max_size=2,
    )
    order_id = None
    try:
        user_id = await create_test_tenant(shared)
        async with shared.acquire() as conn:
            order_id = await source._insert_bare_order(conn, user_id)
            before = await conn.fetchrow(
                "SELECT version, fund_id, portfolio_id FROM orders WHERE order_id = $1", order_id
            )
        await source.test_upgrade_downgrade_upgrade_round_trip_adds_and_removes_columns(pool)
        async with shared.acquire() as conn:
            after = await conn.fetchrow(
                "SELECT version, fund_id, portfolio_id FROM orders WHERE order_id = $1", order_id
            )
        assert after == before, "FA-3 backfill mutated the shared worker order without an event"
    finally:
        try:
            if order_id is not None:
                async with shared.acquire() as conn:
                    await conn.execute("DELETE FROM orders WHERE order_id = $1", order_id)
        finally:
            await shared.close()


async def test_dummy_backfill_is_rejected_by_sentinel_at_source(pool):
    from tests.integration.oms.conftest import insert_event, insert_order
    from tests.support.orders_residue import check_orders

    user_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
        await conn.execute("UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_id)
        await insert_event(
            conn, order_id, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
        )
    database_url = os.environ["DATABASE_URL"]
    await check_orders(database_url, "before dummy FA-3 migration")
    await source.test_upgrade_downgrade_upgrade_round_trip_adds_and_removes_columns(pool)
    with pytest.raises(AssertionError, match="dummy FA-3 writer.*" + str(order_id)):
        await check_orders(database_url, "dummy FA-3 writer")


async def test_eventless_order_update_is_rejected_at_named_source(pool):
    from tests.integration.oms.conftest import insert_order
    from tests.support.orders_residue import check_orders

    user_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
        await conn.execute("UPDATE orders SET price = price + 1 WHERE order_id = $1", order_id)
    with pytest.raises(AssertionError, match="eventless writer.*" + str(order_id)):
        await check_orders(os.environ["DATABASE_URL"], "eventless writer")
