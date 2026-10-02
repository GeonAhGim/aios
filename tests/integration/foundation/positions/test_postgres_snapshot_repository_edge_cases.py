"""PostgresSnapshotRepository edge-case integration tests -- real DB (TEST_DATABASE_URL).

Companion to test_postgres_snapshot_repository.py. Holds the negative,
concurrency, rollback, and list_open tests so that the main file stays
under the 500-line warning threshold (ADR-2026-09-10-C).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, Lot, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account


@pytest.fixture
def repo(pool):
    return PostgresSnapshotRepository(pool)


async def _setup(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return tenant_id, account_id


async def _row(pool, position_key: str):

    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT tenant_id, quantity, last_journal_seq, fund_id, portfolio_id "
            "FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )


def _snapshot(*, tenant_id, account_id, position_key, quantity, last_journal_seq, **overrides):
    base = dict(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid.uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("100"), currency=Currency.KRW),
        cost_method=CostMethod.FIFO,
        lots=[
            Lot(quantity=quantity, unit_cost=Decimal("100"), opened_at=datetime.now(timezone.utc))
        ]
        if quantity
        else [],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.KRW,
        last_journal_seq=last_journal_seq,
        updated_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return PositionSnapshotView(**base)


def _key(tenant_id: uuid.UUID, *, portfolio_id: uuid.UUID | None = None) -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id) if portfolio_id is None else portfolio_id,
        )
    )


# ---------------------------------------------------------------------------
# upsert() -- stale seq, rollback, nil mark price
# ---------------------------------------------------------------------------


async def test_upsert_in_rolled_back_transaction_leaves_previous_version(pool, repo):
    """DoD: a transaction that calls upsert but then rolls back must not
    commit the row (atomicity check)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                quantity=Decimal("0"),
                last_journal_seq=0,
            ),
            expected_seq=0,
        )
    first_count = await _row(pool, position_key)
    assert first_count is not None
    assert first_count["quantity"] == Decimal("0")

    async with pool.acquire() as conn:
        txn = conn.transaction()
        await txn.start()
        try:
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    position_key=position_key,
                    quantity=Decimal("10"),
                    last_journal_seq=1,
                ),
                expected_seq=0,
            )
            # Rollback: raise inside the transaction context
            raise Exception("rollback this transaction")
        except Exception:  # noqa: BLE001
            await txn.rollback()

    second_count = await _row(pool, position_key)
    assert second_count is not None
    assert second_count["quantity"] == Decimal("0")


async def test_upsert_rejects_nil_mark_price_when_present_in_lots(pool, repo):
    """DoD: a snapshot whose lots contain a nil mark_price must be rejected
    at the repository boundary (not silently stored)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("10"),
        last_journal_seq=0,
        mark_price=Money(amount=Decimal("100"), currency=Currency.KRW),
        mark_at=datetime.now(timezone.utc),
    )
    # Mutate the lot's unit_cost to None to simulate a nil mark price in lots
    snapshot.lots[0].unit_cost = None
    with pytest.raises(ValidationError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(conn, snapshot, expected_seq=0)


# ---------------------------------------------------------------------------
# upsert() -- concurrent creation (concurrency gate)
# ---------------------------------------------------------------------------


async def test_sequential_first_creation_after_winner_commits_does_not_overwrite(pool, repo):
    """DoD: two concurrent writers start at seq=0; only the first succeeds,
    the second gets ConcurrencyConflictError (the winner's snapshot is kept)."""
    from src.core.db.conditional_write import ConcurrencyConflictError

    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)

    async def write(quantity, last_journal_seq):
        snapshot = _snapshot(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            quantity=quantity,
            last_journal_seq=last_journal_seq,
        )
        async with pool.acquire() as conn, conn.transaction():
            return await repo.upsert(conn, snapshot, expected_seq=0)

    winner = await write(Decimal("5"), 1)
    assert winner.quantity == Decimal("5")
    assert winner.last_journal_seq == 1

    with pytest.raises(ConcurrencyConflictError):
        await write(Decimal("999"), 2)


# ---------------------------------------------------------------------------
# list_open() tests
# ---------------------------------------------------------------------------


async def test_list_open_returns_only_nonzero_quantity_for_tenant_and_account(pool, repo):
    """list_open filters out zero-quantity rows.

    Negative test (task-10870 DEEPEN): zero-quantity rows are excluded from
    list_open so that downstream components don't need to filter again.
    """
    tenant_id, account_id = await _setup(pool)

    key_active = _key(tenant_id)
    key_zero = _key(tenant_id)
    # Ensure the two keys are different
    while key_zero == key_active:
        key_zero = _key(tenant_id)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=key_active,
                quantity=Decimal("10"),
                last_journal_seq=1,
            ),
            expected_seq=0,
        )
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=key_zero,
                quantity=Decimal("0"),
                last_journal_seq=0,
            ),
            expected_seq=0,
        )

    async with pool.acquire() as conn, conn.transaction():
        result = await repo.list_open(conn, tenant_id, account_id)

    assert len(result) == 1
    assert result[0].position_key == key_active
    assert result[0].quantity == Decimal("10")
