"""EM-3 integration -- `application/aggregate_parent.py` against the real
`orders` table (TEST_DATABASE_URL), proving EM-2's pure rollup/propagation
rules (`domain/parent_child.py`) actually land on `orders.status`/
`filled_quantity`/`committed_child_qty` through `PostgresOrderRepository`.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-3, EM-2 DoD
("집계·전파 규칙, 초과 거부"). task-2121.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderStatus
from src.foundation.ems.application.aggregate_parent import (
    children_awaiting_cancel,
    recompute_parent_aggregate,
    release_reserved_slice,
    reserve_child_slice,
)
from src.foundation.ems.domain.parent_child import AlgoConstraintError, ParentTerminalError
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from tests.integration.oms.conftest import create_test_user


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    status: str = "ACKNOWLEDGED",
    quantity: Decimal = Decimal("10"),
    filled_quantity: Decimal = Decimal("0"),
    committed_child_qty: Decimal = Decimal("0"),
    parent_order_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                committed_child_qty, parent_order_id
            ) VALUES ($1, $2, 'ems-agg-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', $3, $4, $5, $6, $7)
            RETURNING order_id
            """,
            user_id,
            f"ems-agg-{uuid.uuid4().hex}",
            quantity,
            status,
            filled_quantity,
            committed_child_qty,
            parent_order_id,
        )


async def test_recompute_partial_fill_rolls_up_from_one_child(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))
    await _insert_order(
        pool, user_id, status="PARTIALLY_FILLED",
        quantity=Decimal("4"), filled_quantity=Decimal("4"), parent_order_id=parent_id,
    )

    async with pool.acquire() as conn:
        result = await recompute_parent_aggregate(
            repo, conn,
            parent_order_id=parent_id, trace_id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )

    assert result.status is OrderStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("4")


async def test_recompute_full_fill_sums_across_children(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))
    for qty in (Decimal("6"), Decimal("4")):
        await _insert_order(
            pool, user_id, status="FILLED",
            quantity=qty, filled_quantity=qty, parent_order_id=parent_id,
        )

    async with pool.acquire() as conn:
        result = await recompute_parent_aggregate(
            repo, conn,
            parent_order_id=parent_id, trace_id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )

    assert result.status is OrderStatus.FILLED
    assert result.filled_quantity == Decimal("10")


async def test_recompute_all_children_terminal_zero_fill_cancels_parent(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))
    await _insert_order(
        pool, user_id, status="CANCELLED",
        quantity=Decimal("10"), filled_quantity=Decimal("0"), parent_order_id=parent_id,
    )

    async with pool.acquire() as conn:
        result = await recompute_parent_aggregate(
            repo, conn,
            parent_order_id=parent_id, trace_id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )

    assert result.status is OrderStatus.CANCELLED


async def test_recompute_is_a_noop_before_any_child_exists(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        result = await recompute_parent_aggregate(
            repo, conn,
            parent_order_id=parent_id, trace_id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )

    assert result.status is OrderStatus.ACKNOWLEDGED
    assert result.version == 0  # no write happened


async def test_children_awaiting_cancel_excludes_terminal_children(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))
    open_child = await _insert_order(
        pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("3"), parent_order_id=parent_id,
    )
    await _insert_order(
        pool, user_id, status="FILLED",
        quantity=Decimal("7"), filled_quantity=Decimal("7"), parent_order_id=parent_id,
    )

    async with pool.acquire() as conn:
        pending = await children_awaiting_cancel(repo, conn, parent_id)

    assert pending == [open_child]


async def test_reserve_child_slice_rejects_over_commit_against_real_row(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(
        pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"),
        committed_child_qty=Decimal("7"),
    )

    async with pool.acquire() as conn:
        with pytest.raises(AlgoConstraintError):
            await reserve_child_slice(
                repo, conn, parent_order_id=parent_id, new_slice_qty=Decimal("4")
            )

    committed = await pool.fetchval(
        "SELECT committed_child_qty FROM orders WHERE order_id = $1", parent_id
    )
    assert committed == Decimal("7")  # rejected slice never wrote anything


async def test_reserve_child_slice_rejects_terminal_parent_against_real_row(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="FILLED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        with pytest.raises(ParentTerminalError):
            await reserve_child_slice(
                repo, conn, parent_order_id=parent_id, new_slice_qty=Decimal("1")
            )


async def test_reserve_then_release_round_trips_committed_qty(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        reserved = await reserve_child_slice(
            repo, conn, parent_order_id=parent_id, new_slice_qty=Decimal("5")
        )
        assert reserved.committed_child_qty == Decimal("5")

        released = await release_reserved_slice(
            repo, conn, parent_order_id=parent_id, slice_qty=Decimal("5")
        )
        assert released.committed_child_qty == Decimal("0")


async def test_release_more_than_reserved_is_rejected(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(
        pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"),
        committed_child_qty=Decimal("2"),
    )

    async with pool.acquire() as conn:
        with pytest.raises(ValueError):
            await release_reserved_slice(
                repo, conn, parent_order_id=parent_id, slice_qty=Decimal("3")
            )
