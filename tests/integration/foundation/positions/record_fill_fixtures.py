"""LB-11 `record_fill` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.2, §9.3 LB-11.
DoD(task-412): "record_fill 전 케이스(신규·추가매수·부분청산·전량청산·역방향)
+ 감사이벤트 1:1 + 감사 실패 주입 시 저널·스냅샷 롤백".
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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
from src.foundation.positions.contracts.v1 import RecordFillCommand
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
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _command(
    *,
    tenant_id,
    account_id,
    position_key,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal = Decimal("100"),
    fee: Money | None = None,
    order_id=None,
    fill_seq: int = 1,
    occurred_at: datetime = _OCCURRED_AT,
) -> RecordFillCommand:
    return RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=order_id or uuid4(),
        fill_seq=fill_seq,
        side=side,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.KRW),
        fee=fee,
        occurred_at=occurred_at,
        trace_id=uuid4(),
    )


async def _open(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _record(pool, ports, command, *, audit=None):
    async with pool.acquire() as conn, conn.transaction():
        return await record_fill(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=audit or ports.audit,
            clock=_clock,
        )
