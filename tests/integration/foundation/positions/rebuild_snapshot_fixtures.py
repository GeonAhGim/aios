"""LB-13 재빌드 테스트의 실 DB 포트와 체결·펀딩 데이터 구성."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.application.record_funding_fee import record_funding_fee
from src.foundation.positions.contracts.v1 import RecordFillCommand, RecordFundingCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    create_pos_account,
    open_position,
)

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


class _RealPorts:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool: asyncpg.Pool) -> _RealPorts:
    return _RealPorts(pool)


async def _open(pool: asyncpg.Pool) -> tuple[UUID, UUID, str]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _fill(
    pool: asyncpg.Pool,
    ports: _RealPorts,
    *,
    tenant_id: UUID,
    account_id: UUID,
    position_key: str,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    fill_seq: int,
    order_id: UUID,
) -> object:
    async with pool.acquire() as conn, conn.transaction():
        return await record_fill(
            conn,
            RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=order_id,
                fill_seq=fill_seq,
                side=side,
                quantity=quantity,
                price=Money(amount=price, currency=Currency.KRW),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )


async def _funding(
    pool: asyncpg.Pool,
    ports: _RealPorts,
    *,
    tenant_id: UUID,
    account_id: UUID,
    position_key: str,
    amount: Decimal,
    funding_id: str,
) -> object:
    async with pool.acquire() as conn, conn.transaction():
        return await record_funding_fee(
            conn,
            RecordFundingCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                funding_id=funding_id,
                amount=Money(amount=amount, currency=Currency.KRW),
                rate=Decimal("0.0001"),
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )
