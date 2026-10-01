"""LB-15 `compute_daily_nav` 통합테스트 공용 픽스처.

`test_compute_daily_nav*.py` 파일들(핵심 생명주기/동시성/고장주입/성능)이
공유하는 fake 어댑터와 셋업 헬퍼. pytest가 이 파일 자체를 테스트 모듈로
수집하지 않도록 `test_` 접두사를 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg

from src.data.models.base import Currency, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import ComputeDailyNavCommand
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


class FakeCashSource:
    def __init__(self) -> None:
        self._balances: dict[UUID, Decimal | None] = {}

    def seed(self, account_id: UUID, balance: Decimal | None) -> None:
        self._balances[account_id] = balance

    async def cash(self, account_id: UUID, at: datetime) -> Decimal | None:
        return self._balances.get(account_id)


class FakeFxRateSource:
    """이 테스트 스위트는 통화 불일치 케이스를 쓰지 않으므로 호출되면
    그 자체가 결함 신호다."""

    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError(f"FX 경로가 필요 없는 테스트에서 호출됨: {base}->{quote}")


def unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def position_key(tenant_id: UUID, venue_symbol: str) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=venue_symbol,
            strategy_id="default",
            execution_id="paper",
        )
    )


async def open_marked_position(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    quantity: Decimal,
    mark_price: Money | None,
) -> PositionSnapshotView:
    key = position_key(tenant_id, unique_symbol("BTCUSDT"))
    snapshot = PositionSnapshotView(
        position_key=key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=mark_price,
        mark_at=NOW if mark_price is not None else None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def setup_account(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id


def cmd(
    *,
    tenant_id: UUID,
    account_id: UUID,
    at: datetime,
    realized: str = "1000",
    unrealized_delta: str = "0",
    funding: str = "0",
    fees: str = "0",
    flows: str = "0",
) -> ComputeDailyNavCommand:
    return ComputeDailyNavCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        base_currency=Currency.USDT,
        at=at,
        realized=Decimal(realized),
        unrealized_delta=Decimal(unrealized_delta),
        funding=Decimal(funding),
        fees=Decimal(fees),
        flows=Decimal(flows),
        trace_id=uuid4(),
    )
