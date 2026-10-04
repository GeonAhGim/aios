# loc-allow: shared fixtures/helpers for legacy_positions_projection integration tests
"""Shared fixtures and helpers for LegacyPositionsProjection tests.

Imported by test_legacy_compat.py and test_legacy_compat_edge_cases.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id
from src.foundation.positions.adapters.legacy_positions_projection import (
    LegacyPositionsProjection,
)
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_EXCHANGE = "TESTEX"
_EXCHANGE_B = "TESTEX-B"


@pytest.fixture
def projection() -> LegacyPositionsProjection:
    return LegacyPositionsProjection()


async def _seed_linked_pair(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    realized_pnl: Decimal = Decimal("0"),
    closed_at: datetime | None = None,
    exchange: str = _EXCHANGE,
) -> tuple[int, str]:
    """legacy `positions` 행 + `legacy_position_id`로 그 행을 가리키는
    `pos_snapshot` 행을 짝으로 만든다."""
    async with pool.acquire() as conn:
        legacy_id: int = await conn.fetchval(
            """
            INSERT INTO positions (
                user_id, symbol, exchange, strategy_id, quantity,
                average_entry_price, realized_pnl, entry_time, closed_at
            ) VALUES ($1, $2, $3, 'test-strategy', $4, $5, $6, now(), $7)
            RETURNING id
            """,
            tenant_id,
            symbol,
            exchange,
            quantity,
            price,
            realized_pnl,
            closed_at,
        )
        position_key = _snapshot_key(tenant_id, venue=exchange)
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at,
                fund_id, portfolio_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'FIFO', $7::jsonb, $8, NULL, 0, 0,
                NULL, NULL, 1, $9, now(), $10, $11
            )
            """,
            position_key,
            tenant_id,
            account_id,
            uuid.uuid4(),
            quantity,
            price,
            json.dumps([]),
            realized_pnl,
            legacy_id,
            default_fund_id(tenant_id),
            default_portfolio_id(tenant_id),
        )
    return legacy_id, position_key


def _snapshot_key(tenant_id: UUID, *, venue: str = _EXCHANGE) -> str:
    return str(
        PositionKey(
            venue=venue,
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="test-strategy",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )


async def _direct_legacy_query(
    pool: asyncpg.Pool, *, user_id: UUID, symbol: str
) -> list[asyncpg.Record]:
    """구 경로 대조군 — 어댑터 없이 `positions`를 직접 읽는다."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id AS legacy_position_id, quantity, average_entry_price,
                   realized_pnl, unrealized_pnl, closed_at
            FROM positions
            WHERE user_id = $1 AND symbol = $2 AND exchange = $3
            ORDER BY entry_time ASC
            """,
            user_id,
            symbol,
            _EXCHANGE,
        )


async def _setup_account(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return tenant_id, account_id


async def _project(projection: LegacyPositionsProjection, pool, *, user_id, symbol):
    async with pool.acquire() as conn, conn.transaction():
        return await projection.get_positions(
            conn, user_id=user_id, symbol=symbol, exchange=_EXCHANGE
        )
