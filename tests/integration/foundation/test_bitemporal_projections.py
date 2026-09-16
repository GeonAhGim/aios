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
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )


async def test_aios_app_cannot_update_pos_snapshot(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _snapshot_key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)

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
            conn,
            account_code,
            delta_balance=Decimal("50"),
            delta_held=Decimal("0"),
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


# -- D2-deepen: failure-injection, numerical assertion, gate-red reproduction --


async def test_failure_injection_no_update_trigger_blocks_table_owner_on_pos_snapshot(
    pool: asyncpg.Pool,
) -> None:
    """REVOKE UPDATE never binds the owning role (PostgreSQL: the owner
    always retains implicit privileges) -- `no_update_guard.py`'s own
    docstring claims the *trigger*, not REVOKE, is what makes UPDATE
    physically impossible even for the owner. This is a failure-injection
    test for that specific claim: skip `SET ROLE aios_app` entirely and
    issue the UPDATE as the migration/superuser connection that actually
    owns `pos_snapshot` -- if the trigger alone still blocks it, the
    defense-in-depth claim is real, not just documentation (mirrors
    `test_worm_trigger_blocks_table_owner_on_audit_log` in
    `tests/integration/test_db_roles.py` for the sibling WORM guard)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _snapshot_key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)

    with pytest.raises(asyncpg.RaiseError, match="no-update violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE pos_snapshot SET quantity = 1 WHERE position_key = $1", position_key
            )


async def test_numerical_assertion_ledger_balance_survives_many_sequential_deltas_no_drift(
    pool: asyncpg.Pool,
) -> None:
    """FA-10's write path replaces the `ledger_balance` row via DELETE+INSERT
    on every `apply()` call instead of UPDATE-in-place. Numerical invariant:
    run 20 real DELETE+INSERT cycles with distinct Decimal deltas and assert
    the final balance equals the *exact* Decimal sum of all deltas (no float
    drift across repeated real-DB round trips), `last_entry_seq` advanced by
    exactly 20, and the natural key still resolves to exactly one row -- the
    projection table's PK (unlike a real bitemporal history table) can never
    hold more than one row per key, since DELETE removes the old version
    before INSERT adds the new one."""
    account_code = await create_ledger_account(pool, initial_balance=Decimal("100"))
    repo = PostgresBalanceRepository(pool)

    deltas = [Decimal(i + 1) * Decimal("0.13") * (1 if i % 2 == 0 else -1) for i in range(20)]
    expected_balance = Decimal("100") + sum(deltas)

    for seq, delta in enumerate(deltas):
        async with pool.acquire() as conn, conn.transaction():
            await repo.apply(
                conn, account_code, delta_balance=delta, delta_held=Decimal("0"), expected_seq=seq
            )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT lb.balance, lb.last_entry_seq, lb.tx_to FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id "
            "WHERE la.account_code = $1",
            account_code,
        )

    assert len(rows) == 1, "DELETE+INSERT must never leave more than one row per account"
    assert rows[0]["balance"] == expected_balance, (
        f"balance {rows[0]['balance']} != exact expected {expected_balance} -- Decimal drift "
        "across 20 DELETE+INSERT cycles"
    )
    assert rows[0]["last_entry_seq"] == 20
    assert rows[0]["tx_to"] is None


async def test_gate_red_reproduction_bulk_update_across_multiple_positions_blocked(
    pool: asyncpg.Pool,
) -> None:
    """Reproduce the exact incident class FA-10 exists to prevent: a
    maintenance/backfill script that "fixes" many rows in one UPDATE
    statement, not just a single row by primary key. Insert three
    `positions` rows for the same tenant and issue one bulk UPDATE with no
    id filter -- the BEFORE UPDATE trigger fires on the first row touched
    and aborts the *entire* statement, so this is gate-red (all-or-nothing
    rejection), not a partial write that would silently corrupt some rows
    while "only" failing on the last one."""
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        legacy_ids = [
            await conn.fetchval(
                "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
                "average_entry_price, entry_time) "
                "VALUES ($1, $2, 'TESTEX', 'test-strategy', 1, 1, now()) RETURNING id",
                tenant_id,
                f"SYM{i}",
            )
            for i in range(3)
        ]

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("UPDATE positions SET quantity = 999 WHERE user_id = $1", tenant_id)
    _assert_no_update_violation(exc_info)

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT quantity FROM positions WHERE id = ANY($1)", legacy_ids)
    assert [r["quantity"] for r in rows] == [Decimal("1")] * 3, (
        "bulk UPDATE must be rejected atomically -- no row may end up partially updated"
    )
