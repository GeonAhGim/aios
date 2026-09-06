"""FA-4(963d5f3cfb1b) 마이그레이션 실DB 왕복·FK·백필(비-WORM 테이블) 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4 DoD
("왕복(up/down/up) 통과 + 존재하지 않는 fund_id INSERT가 FK로 거부되는
negative test 1건 + 테이블별 백필 행수/NULL 잔여 행수를 각각 수치로
단언"). subprocess로 alembic을 띄우는 이유·DSN 해석은 FA-3
(`test_migration_fa3_orders_fills_columns.py`)와 동일 패턴을 따른다.

`pos_journal`·`ledger_journal_entry`·`ledger_posting_line`(WORM, 백필 없음)의
검증은 `test_migration_fa4_worm_no_backfill.py`가 담당한다 — 이 파일은
컬럼 왕복(5개 테이블 전부)과 `pos_account`·`pos_snapshot`(비-WORM, 백필
대상)만 다룬다."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
)
from tests.integration.conftest import create_test_tenant

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "789c138f13fe"

_ALL_TABLES = (
    "pos_account",
    "pos_journal",
    "pos_snapshot",
    "ledger_journal_entry",
    "ledger_posting_line",
)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _column_exists(pool: asyncpg.Pool, table: str, column: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
    return row is not None


async def _bootstrap_hierarchy(pool: asyncpg.Pool, tenant_id) -> None:
    repo = PostgresEntityRepository(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=default_entity_id(tenant_id),
            tenant_id=tenant_id,
            name="FA-4 Test Entity",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=default_fund_id(tenant_id),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    await repo.create_portfolio(
        Portfolio(
            portfolio_id=default_portfolio_id(tenant_id),
            fund_id=fund.fund_id,
            venue_account_ref=f"fa4-test-venue-{uuid4().hex[:8]}",
        )
    )


async def test_upgrade_downgrade_upgrade_round_trip_adds_and_removes_columns(pool):
    for table in _ALL_TABLES:
        for column in ("fund_id", "portfolio_id"):
            assert await _column_exists(pool, table, column)

    _run_alembic("downgrade", _DOWN_REVISION)
    for table in _ALL_TABLES:
        for column in ("fund_id", "portfolio_id"):
            assert not await _column_exists(pool, table, column)

    _run_alembic("upgrade", "head")
    for table in _ALL_TABLES:
        for column in ("fund_id", "portfolio_id"):
            assert await _column_exists(pool, table, column)


async def test_negative_insert_with_nonexistent_fund_id_rejected_by_fk(pool):
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method, fund_id) "
                "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO', $2)",
                tenant_id,
                uuid4(),
            )


async def test_backfill_computes_default_ids_for_bootstrapped_user_and_nulls_the_rest(pool):
    bootstrapped_user = await create_test_tenant(pool)
    bare_user = await create_test_tenant(pool)
    await _bootstrap_hierarchy(pool, bootstrapped_user)

    _run_alembic("downgrade", _DOWN_REVISION)
    async with pool.acquire() as conn:
        bootstrapped_account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            bootstrapped_user,
        )
        bare_account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            bare_user,
        )
        bootstrapped_snapshot_key = f"fa4-test-{uuid4().hex}"
        bare_snapshot_key = f"fa4-test-{uuid4().hex}"
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            bootstrapped_snapshot_key,
            bootstrapped_user,
            bootstrapped_account_id,
            uuid4(),
        )
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            bare_snapshot_key,
            bare_user,
            bare_account_id,
            uuid4(),
        )

    _run_alembic("upgrade", "head")

    async with pool.acquire() as conn:
        pos_account_backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_account WHERE account_id = ANY($1::uuid[]) "
            "AND fund_id IS NOT NULL",
            [bootstrapped_account_id, bare_account_id],
        )
        pos_account_null = await conn.fetchval(
            "SELECT count(*) FROM pos_account WHERE account_id = ANY($1::uuid[]) "
            "AND fund_id IS NULL",
            [bootstrapped_account_id, bare_account_id],
        )
        pos_snapshot_backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE position_key = ANY($1::varchar[]) "
            "AND fund_id IS NOT NULL",
            [bootstrapped_snapshot_key, bare_snapshot_key],
        )
        pos_snapshot_null = await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE position_key = ANY($1::varchar[]) "
            "AND fund_id IS NULL",
            [bootstrapped_snapshot_key, bare_snapshot_key],
        )
        bootstrapped_account_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_account WHERE account_id = $1",
            bootstrapped_account_id,
        )
        bootstrapped_snapshot_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_snapshot WHERE position_key = $1",
            bootstrapped_snapshot_key,
        )
        bare_account_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_account WHERE account_id = $1",
            bare_account_id,
        )

    assert pos_account_backfilled == 1
    assert pos_account_null == 1
    assert pos_snapshot_backfilled == 1
    assert pos_snapshot_null == 1
    assert bootstrapped_account_row["fund_id"] == default_fund_id(bootstrapped_user)
    assert bootstrapped_account_row["portfolio_id"] == default_portfolio_id(bootstrapped_user)
    assert bootstrapped_snapshot_row["fund_id"] == default_fund_id(bootstrapped_user)
    assert bootstrapped_snapshot_row["portfolio_id"] == default_portfolio_id(bootstrapped_user)
    assert bare_account_row["fund_id"] is None
    assert bare_account_row["portfolio_id"] is None
