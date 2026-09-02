"""LegacyPositionsProjection 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-10.
DoD(task-376): 동일 계정·심볼에 대해 legacy `positions` 직접 쿼리 결과와
`LegacyPositionsProjection` 투영 결과가 수량·평단·실현손익까지 일치
(부분청산 후 포함), 대응 legacy 행이 없으면 빈 결과(예외 아님).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.positions.adapters.legacy_positions_projection import (
    LegacyPositionsProjection,
)
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account

_EXCHANGE = "TESTEX"


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
) -> tuple[int, str]:
    """legacy `positions` 행 + `legacy_position_id`로 그 행을 가리키는
    `pos_snapshot` 행을 짝으로 만든다(어댑터 대신 픽스처가 직접 INSERT
    — LB-9 `upsert`는 `legacy_position_id`를 채우지 않음)."""
    async with pool.acquire() as conn:
        legacy_id: int = await conn.fetchval(
            """
            INSERT INTO positions (
                user_id, symbol, exchange, strategy_id, quantity,
                average_entry_price, realized_pnl, entry_time, closed_at
            ) VALUES ($1, $2, $3, 'test-strategy', $4, $5, $6, now(), $7)
            RETURNING id
            """,
            tenant_id, symbol, _EXCHANGE, quantity, price, realized_pnl, closed_at,
        )
        position_key = f"pos:{uuid.uuid4().hex}"
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'FIFO', $7::jsonb, $8, NULL, 0, 0,
                NULL, NULL, 1, $9, now()
            )
            """,
            position_key, tenant_id, account_id, uuid.uuid4(), quantity, price,
            json.dumps([]), realized_pnl, legacy_id,
        )
    return legacy_id, position_key


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
            user_id, symbol, _EXCHANGE,
        )


async def _setup_account(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return tenant_id, account_id


async def _project(projection: LegacyPositionsProjection, pool, *, user_id, symbol):
    async with pool.acquire() as conn, conn.transaction():
        return await projection.get_positions(
            conn, user_id=user_id, symbol=symbol, exchange=_EXCHANGE
        )


async def test_open_position_matches_legacy_query(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool, tenant_id=tenant_id, account_id=account_id, symbol=symbol,
        quantity=Decimal("2.5"), price=Decimal("100.1234567890"),
    )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    legacy_row, projected_row = legacy_rows[0], projected[0]
    assert projected_row.legacy_position_id == legacy_row["legacy_position_id"]
    assert projected_row.quantity == legacy_row["quantity"]
    assert projected_row.average_entry_price == legacy_row["average_entry_price"]
    assert projected_row.realized_pnl == legacy_row["realized_pnl"]
    assert projected_row.unrealized_pnl == legacy_row["unrealized_pnl"]


async def test_partial_close_still_matches_legacy_query(pool, projection):
    """부분청산 후: 수량이 줄고 실현손익이 누적된 상태도 일치해야 한다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id, position_key = await _seed_linked_pair(
        pool, tenant_id=tenant_id, account_id=account_id, symbol=symbol,
        quantity=Decimal("10"), price=Decimal("50"),
    )

    # 부분청산: 10 중 4를 60에 매도 → 남은 수량 6, 실현손익 = 4*(60-50) = 40
    partial_quantity, partial_realized = Decimal("6"), Decimal("40")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE positions SET quantity = $2, realized_pnl = $3 WHERE id = $1",
            legacy_id, partial_quantity, partial_realized,
        )
        await conn.execute(
            "UPDATE pos_snapshot SET quantity = $2, realized_pnl_base = $3, "
            "last_journal_seq = 2 WHERE position_key = $1",
            position_key, partial_quantity, partial_realized,
        )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    assert projected[0].quantity == partial_quantity == legacy_rows[0]["quantity"]
    assert projected[0].realized_pnl == partial_realized == legacy_rows[0]["realized_pnl"]
    assert projected[0].average_entry_price == legacy_rows[0]["average_entry_price"]


async def test_closed_position_reports_closed_at_and_matches_legacy(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool, tenant_id=tenant_id, account_id=account_id, symbol=symbol,
        quantity=Decimal("0"), price=Decimal("20"), realized_pnl=Decimal("15"),
        closed_at=datetime.now(timezone.utc),
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].closed_at is not None
    assert projected[0].realized_pnl == Decimal("15")


async def test_no_linked_legacy_row_returns_empty_not_exception(pool, projection):
    """스냅샷이 있어도 `legacy_position_id`가 비어 있으면(아직 연결 안 됨)
    빈 리스트를 반환한다 — 예외가 아니다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at
            ) VALUES (
                $1, $2, $3, $4, 3, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                NULL, NULL, 1, NULL, now()
            )
            """,
            f"pos:{uuid.uuid4().hex}", tenant_id, account_id, uuid.uuid4(), json.dumps([]),
        )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)
    assert projected == []


async def test_no_matching_symbol_returns_empty_not_exception(pool, projection):
    """해당 계정·심볼 조합의 legacy 행이 아예 없으면 빈 리스트."""
    tenant_id, _ = await _setup_account(pool)
    projected = await _project(projection, pool, user_id=tenant_id, symbol="NEVER-EXISTED")
    assert projected == []
