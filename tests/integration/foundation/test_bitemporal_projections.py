"""FA-10 integration tests -- bitemporal columns + no-UPDATE trigger on
pos_snapshot, ledger_balance, positions.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10.
DoD(task-2051): (1) an UPDATE attempt against any of the three tables from
the `aios_app` role raises. (3) the `_current` view (`tx_to IS NULL`
filter) matches the underlying data row-for-row and value-for-value after a
real write. (4) the write paths (position snapshot fold, balance apply)
switched from UPDATE to DELETE+INSERT but still leave exactly one row per
natural key -- no observable difference from a real UPDATE.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.ledger.conftest import create_ledger_account
from tests.integration.foundation.positions.conftest import (
    create_pos_account,
    force_row_replace,
    open_position,
)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


def _assert_no_update_violation(exc_info: pytest.ExceptionInfo) -> None:
    """The no-update defense is two layers deep (REVOKE + trigger), same
    rationale as `tests/integration/test_db_roles.py` -- only assert the
    message when the trigger is the one that actually fired."""
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "no-update violation" in str(exc_info.value)


def _snapshot_key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            venue="TESTVENUE", instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default", execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )


async def test_aios_app_cannot_update_pos_snapshot(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _snapshot_key(tenant_id)
    await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE pos_snapshot SET quantity = 1 WHERE position_key = $1", position_key
            )
    _assert_no_update_violation(exc_info)


async def test_aios_app_cannot_update_ledger_balance(pool: asyncpg.Pool) -> None:
    account_code = await create_ledger_account(pool)

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE ledger_balance SET balance = 1 "
                "FROM ledger_account "
                "WHERE ledger_account.account_id = ledger_balance.account_id "
                "AND ledger_account.account_code = $1",
                account_code,
            )
    _assert_no_update_violation(exc_info)


async def test_aios_app_cannot_update_positions(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        legacy_id = await conn.fetchval(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, 'SYM', 'TESTEX', 'test-strategy', 1, 1, now()) RETURNING id",
            tenant_id,
        )

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("UPDATE positions SET quantity = 2 WHERE id = $1", legacy_id)
    _assert_no_update_violation(exc_info)


async def test_pos_snapshot_fold_keeps_single_current_row(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _snapshot_key(tenant_id)
    snapshot = await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )

    repo = PostgresSnapshotRepository(pool)
    updated = snapshot.model_copy(update={"quantity": Decimal("5"), "last_journal_seq": 1})
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, updated, expected_seq=0)

    async with pool.acquire() as conn:
        base_rows = await conn.fetch(
            "SELECT quantity, tx_to FROM pos_snapshot WHERE position_key = $1", position_key
        )
        current_rows = await conn.fetch(
            "SELECT quantity, tx_to FROM pos_snapshot_current WHERE position_key = $1",
            position_key,
        )

    assert [dict(r) for r in base_rows] == [{"quantity": Decimal("5"), "tx_to": None}]
    assert [dict(r) for r in current_rows] == [dict(r) for r in base_rows]


async def test_ledger_balance_apply_keeps_single_current_row(pool: asyncpg.Pool) -> None:
    account_code = await create_ledger_account(pool, initial_balance=Decimal("100"))
    repo = PostgresBalanceRepository(pool)

    async with pool.acquire() as conn, conn.transaction():
        await repo.apply(
            conn, account_code, delta_balance=Decimal("50"), delta_held=Decimal("0"),
            expected_seq=0,
        )

    async with pool.acquire() as conn:
        base_rows = await conn.fetch(
            "SELECT lb.balance, lb.tx_to FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id "
            "WHERE la.account_code = $1",
            account_code,
        )
        current_rows = await conn.fetch(
            "SELECT lbc.balance, lbc.tx_to FROM ledger_balance_current lbc "
            "JOIN ledger_account la ON la.account_id = lbc.account_id "
            "WHERE la.account_code = $1",
            account_code,
        )

    assert len(base_rows) == 1
    assert base_rows[0]["balance"] == Decimal("150")
    assert base_rows[0]["tx_to"] is None
    assert [dict(r) for r in current_rows] == [dict(r) for r in base_rows]


async def test_positions_replace_keeps_single_current_row(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        legacy_id = await conn.fetchval(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, 'SYM', 'TESTEX', 'test-strategy', 1, 1, now()) RETURNING id",
            tenant_id,
        )

    await force_row_replace(
        pool, table="positions", id_column="id", id_value=legacy_id, quantity=Decimal("7")
    )

    async with pool.acquire() as conn:
        base_rows = await conn.fetch(
            "SELECT id, quantity, tx_to FROM positions WHERE id = $1", legacy_id
        )
        current_rows = await conn.fetch(
            "SELECT id, quantity, tx_to FROM positions_current WHERE id = $1", legacy_id
        )

    assert [dict(r) for r in base_rows] == [
        {"id": legacy_id, "quantity": Decimal("7"), "tx_to": None}
    ]
    assert [dict(r) for r in current_rows] == [dict(r) for r in base_rows]
