"""FA-0a batch B — tenant_id FK가 tenant(id)를 실제로 강제하는지 실DB로 확인.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md §9 FA-0a.
`test_tenant_fk_enforced_batch_a.py`(배치 A)와 같은 형태 — (a) 존재하지
않는 tenant_id로 INSERT하면 `asyncpg.ForeignKeyViolationError`가 나는지,
(b) 존재하는 tenant_id로는 같은 INSERT가 성공하는지(대조군), (c) 그 FK
제약이 실제로 `tenant(id)`를 가리키는지(pg_constraint)를 단언한다.
배치 B의 4파일(positions_journal/ledger_core/md_candles/
md_ingest_batch_tick, 6곳) 중 대표로 `pos_account`(NOT NULL,
positions_journal)와 `ledger_account`(NULLABLE, ledger_core)를
검증한다 — 나머지 4곳(pos_journal/pos_snapshot/md_ingest_batch/
md_ingest_batch_tick)은 같은 백필 함수(`_backfill`)로 동일하게 교정되고
마이그레이션 자신의 왕복 테스트가 스키마 정의를 커버한다.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _cleanup_batch_b_test_accounts(pool: asyncpg.Pool):
    """`test_ledger_account_null_tenant_id_still_allowed` commits a
    `TEST:BATCH-B-NULL:*` row directly via `pool.acquire()` (no rollback) —
    left uncleaned it survives into any later test in the same
    TEST_DATABASE_URL session that replays the full migration chain
    (e.g. FA-0c's `18965d657219` backfill, task-1942), whose
    `chart_of_accounts.default_scope()` hard-fails on a non-`USER:`/`PLATFORM:`
    account_code (same hygiene rule as test_fa0c_account_scope.py's
    `_cleanup_portfolio_test_accounts`)."""
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM ledger_account WHERE account_code LIKE 'TEST:BATCH-B-%'")


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


async def test_pos_account_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "pos_account", "pos_account_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_pos_account_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method)
                VALUES ($1, 'binance', 'KRW', 'FIFO')
                """,
                missing_tenant_id,
            )


async def test_pos_account_insert_with_existing_tenant_id_succeeds(pool):
    tenant_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method)
            VALUES ($1, 'binance', 'KRW', 'FIFO')
            RETURNING tenant_id
            """,
            tenant_id,
        )

    assert row["tenant_id"] == tenant_id


async def test_ledger_account_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "ledger_account", "ledger_account_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_ledger_account_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ledger_account (tenant_id, account_code, account_type, currency)
                VALUES ($1, $2, 'ASSET', 'KRW')
                """,
                missing_tenant_id,
                f"TEST:BATCH-B-ORPHAN:{missing_tenant_id}",
            )


async def test_ledger_account_null_tenant_id_still_allowed(pool):
    # NULL은 FK 체크 대상이 아니다(플랫폼 계정 등, ledger_core 원본
    # `tenant_id UUID REFERENCES users(user_id)`에 NOT NULL이 없었다) — FK를
    # tenant(id)로 옮긴 뒤에도 이 예외가 유지되는지 대조군으로 확인한다.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ledger_account (tenant_id, account_code, account_type, currency)
            VALUES (NULL, $1, 'ASSET', 'KRW')
            RETURNING tenant_id
            """,
            f"TEST:BATCH-B-NULL:{uuid4()}",
        )

    assert row["tenant_id"] is None
