"""Shared fixtures for LB-16 `reconcile_provider` integration tests.

Imported by test_reconcile_provider.py and test_reconcile_provider_edge.py.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.data.models.base import Currency, Money
from src.data.models.trading import AccountBalance
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository

# -- public fixture exports --
# These are re-exported so consumers don't need to import from this internal module directly.
__all__ = [
    "FakeAdapter",
    "_recon",
    "_balance",
    "_open_position",
]


class FakeAdapter:
    """`ExchangeAdapter.get_balance`만 흉내내는 대역 — 나머지 추상 메서드는
    이 리프가 호출하지 않는다."""

    def __init__(
        self, balances: list[AccountBalance] | None = None, error: Exception | None = None
    ) -> None:
        self._balances = balances or []
        self._error = error

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        if self._error is not None:
            raise self._error
        return self._balances


def _recon(pool):
    return functools.partial(
        run_reconciliation,
        PostgresReconciliationRepository(pool),
        PostgresConnectionRepository(pool),
        PostgresRiskGateRepository(pool),
    )


def _balance(asset: str, amount: Decimal) -> AccountBalance:
    return AccountBalance(exchange="bitget", asset=asset, total=amount, available=amount)


async def _open_position(
    pool, *, tenant_id, account_id, asset: str, quantity: Decimal
) -> PositionSnapshotView:
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=asset,
            strategy_id="default",
            execution_id="p1",
        )
    )
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("1"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)
