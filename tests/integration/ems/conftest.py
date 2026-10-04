"""Shared pytest fixtures for EMS integration tests."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    ParentOrder,
    ParentOrderConstraints,
)
from src.services.oms.contracts.v1_views import OrderView

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


def _parent_order(parent_id: uuid.UUID, *, qty: Decimal) -> ParentOrder:
    return ParentOrder(
        parent_id=parent_id,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        qty=qty,
        algo=AlgoSpec(
            kind=AlgoKind.TWAP,
            start=_T0,
            end=_T0 + timedelta(minutes=10),
            max_participation_pct=Decimal("10"),
            slice_interval_sec=60,
            urgency=Decimal("0.5"),
            seed=42,
        ),
        constraints=ParentOrderConstraints(max_participation_pct=Decimal("10")),
        fund_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        arrival_ts=_T0,
        status=OrderStatus.ACKNOWLEDGED,
    )


def _child(
    parent_id: uuid.UUID, slice_seq: int, qty: Decimal, *, scheduled_at: datetime
) -> ChildOrder:
    return ChildOrder(
        child_id=uuid.uuid4(),
        parent_id=parent_id,
        slice_seq=slice_seq,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        planned_qty=qty,
        scheduled_at=scheduled_at,
    )


def _order_view(order_id: uuid.UUID) -> OrderView:
    now = datetime.now(timezone.utc)
    return OrderView(
        order_id=order_id,
        tenant_id=uuid.uuid4(),
        execution_id=None,
        client_order_id=f"fake-{order_id.hex}",
        exchange_order_id=None,
        symbol="BTC/USDT",
        venue_symbol=None,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force="GTC",
        quantity=Decimal("1"),
        price=None,
        status=OrderStatus.VALIDATED,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=0,
        parent_order_id=None,
        algo_run_id=None,
        unknown_since=None,
        provider_order_date=None,
        created_at=now,
        updated_at=now,
    )


@dataclass
class _RecordingSubmitter:
    """Fake `submit_child` -- returns a canned `OrderView` per call unless
    `fail_seqs` says this `slice_seq` should raise instead."""

    fail_with: dict[int, Exception] = field(default_factory=dict)
    calls: list[int] = field(default_factory=list)

    async def __call__(self, child: ChildOrder) -> OrderView:
        self.calls.append(child.slice_seq)
        if child.slice_seq in self.fail_with:
            raise self.fail_with[child.slice_seq]
        return _order_view(uuid.uuid4())


# -- helpers used by both test_algo_lifecycle.py and test_algo_cancel.py --


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    order_id: uuid.UUID | None = None,
    status: str = "ACKNOWLEDGED",
    quantity: Decimal = Decimal("10"),
    committed_child_qty: Decimal = Decimal("0"),
    parent_order_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                order_id, user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                committed_child_qty, parent_order_id
            ) VALUES (COALESCE($1, gen_random_uuid()), $2, $3, 'ems-em15-test', '1.0.0',
                      'BTC/USDT', 'bitget', 'BUY', 'LIMIT', $4, $5, 0, $6, $7)
            RETURNING order_id
            """,
            order_id,
            user_id,
            f"ems-em15-{uuid.uuid4().hex}",
            quantity,
            status,
            committed_child_qty,
            parent_order_id,
        )
    return row["order_id"]


async def _committed_qty(pool: asyncpg.Pool, order_id: uuid.UUID) -> Decimal:
    return await pool.fetchval(
        "SELECT committed_child_qty FROM orders WHERE order_id = $1", order_id
    )
