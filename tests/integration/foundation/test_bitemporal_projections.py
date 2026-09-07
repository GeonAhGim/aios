"""FA-10 통합테스트 -- pos_snapshot·ledger_balance·positions 양시간축 + UPDATE 금지.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10.
DoD(task-2051): (1) `aios_app` 롤로 세 테이블 각각 UPDATE를 시도하면
예외가 난다. (3) `_current` 뷰(`tx_to IS NULL` 필터)가 실제 쓰기 뒤에도
기존 데이터와 행 수·값 단위로 동일하다. (4) 쓰기 경로(포지션 스냅샷
fold·잔액 갱신)가 UPDATE 대신 DELETE+INSERT로 바뀌었지만 자연키당 정확히
1행만 남는다 -- 실제 UPDATE와 관측 가능한 차이가 없다.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
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
    """no-update 방어는 REVOKE·트리거 두 층이다(`tests/integration/test_db_roles.py`와
    동일 근거) -- 트리거가 실제로 발동한 경우에 한해 메시지를 확인한다."""
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "no-update violation" in str(exc_info.value)


async def test_aios_app_cannot_update_pos_snapshot(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = f"pos:{uuid.uuid4().hex}"
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
    position_key = f"pos:{uuid.uuid4().hex}"
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
