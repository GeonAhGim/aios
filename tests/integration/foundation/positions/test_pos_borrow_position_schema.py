"""LA-25 — pos_borrow_position 스키마 실DB 검증(ddbd3a33bf30).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LA-25,
task-1752 decision. (a) tenant_id FK가 실제로 `tenant(id)`를 가리키는지,
(b) 존재하지 않는 tenant_id INSERT가 FK 위반으로 거부되는지(negative),
(c) RLS `tenant_isolation`이 교차 테넌트 SELECT를 0행으로 막는지를
`tests/integration/foundation/test_tenant_fk_enforced_batch_a.py`·
`tests/integration/core/db/test_rls_foundation_fa0a_batch_a.py`와
동일한 형태로 단언한다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.core.db.conftest import AppRoleTx


async def _fk_ref(pool: asyncpg.Pool, table: str, constraint: str) -> tuple[str, str] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT confrelid::regclass::text AS ref_table, a.attname AS ref_column
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.confrelid AND a.attnum = ANY(c.confkey)
            WHERE c.conrelid = $1::regclass AND c.contype = 'f' AND c.conname = $2
            """,
            table,
            constraint,
        )
    return (row["ref_table"], row["ref_column"]) if row is not None else None


async def _seed_borrow_position(
    pool: asyncpg.Pool, tenant_id: UUID, *, position_key: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pos_borrow_position
                (position_key, tenant_id, short_quantity, supply_rate, currency)
            VALUES ($1, $2, 100, 0.03, 'USDT')
            """,
            position_key,
            tenant_id,
        )


async def test_pos_borrow_position_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "pos_borrow_position", "pos_borrow_position_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_pos_borrow_position_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, 0.03, 'USDT')
                """,
                f"BORROW-ORPHAN-{uuid4().hex}",
                missing_tenant_id,
            )


async def test_pos_borrow_position_insert_with_existing_tenant_id_succeeds(pool):
    tenant_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO pos_borrow_position
                (position_key, tenant_id, short_quantity, supply_rate, currency)
            VALUES ($1, $2, 100, 0.03, 'USDT')
            RETURNING tenant_id
            """,
            f"BORROW-CONTROL-{uuid4().hex}",
            tenant_id,
        )

    assert row["tenant_id"] == tenant_id


async def test_pos_borrow_position_cross_tenant_select_returns_zero_rows(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_borrow_position(pool, tenant_a, position_key=f"BORROW-A-{uuid4().hex}")
    await _seed_borrow_position(pool, tenant_b, position_key=f"BORROW-B-{uuid4().hex}")

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM pos_borrow_position")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_pos_borrow_position_select_bound_to_other_tenant_excludes_all(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_borrow_position(pool, tenant_a, position_key=f"BORROW-C-{uuid4().hex}")

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_b):
        rows = await conn.fetch(
            "SELECT tenant_id FROM pos_borrow_position WHERE tenant_id = $1", tenant_a
        )

    assert rows == []
