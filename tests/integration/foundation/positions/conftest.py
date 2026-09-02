"""LB-9 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 그 값을 asyncpg DSN으로 변환하고 `pos_account`/
`pos_snapshot`(어댑터가 검증하는 최소 선행 상태) 생성 헬퍼만 둔다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView


def _asyncpg_dsn() -> str:
    import os

    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=64)
    yield p
    await p.close()


async def create_pos_account(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    venue: str = "TESTVENUE",
    base_currency: Currency = Currency.KRW,
    cost_method: CostMethod = CostMethod.FIFO,
) -> UUID:
    async with pool.acquire() as conn:
        account_id: UUID = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            tenant_id,
            venue,
            base_currency.value,
            cost_method.value,
        )
    return account_id


async def open_position(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    position_key: str,
    base_currency: Currency = Currency.KRW,
    cost_method: CostMethod = CostMethod.FIFO,
) -> PositionSnapshotView:
    """저널 append의 전제(도크스트링, `postgres_journal_repository.py`)인
    빈 초기 스냅샷을 만든다 — `expected_seq=0`인 최초 `upsert`."""
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid.uuid4(),
        quantity=Decimal("0"),
        avg_cost=Money(amount=Decimal("0"), currency=base_currency),
        cost_method=cost_method,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=base_currency,
        last_journal_seq=0,
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)
