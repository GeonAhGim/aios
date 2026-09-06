"""FA-3(789c138f13fe) 마이그레이션 실DB 왕복·백필 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-3 DoD
("upgrade→downgrade→upgrade 왕복 통과, 존재하지 않는 fund_id INSERT 1건이
FK로 거부되는 negative test, 백필된 행 수와 NULL 잔여 행 수를 각각 수치로
단언하는 테스트"). subprocess로 alembic을 띄우는 이유·DSN 해석은
`test_migration_roundtrip.py`(FA-2)와 동일 패턴을 따른다."""
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
from tests.integration.conftest import create_test_tenant, create_test_user

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "c9f4e2a1b6d7"


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


async def _insert_bare_order(conn: asyncpg.Connection, user_id) -> object:
    """`fund_id`/`portfolio_id` 컬럼이 아직 없는(FA-3 이전) 스키마 상태를
    가정한 최소 INSERT — 백필 대상 원본 행을 만든다."""
    return await conn.fetchval(
        """
        INSERT INTO orders (
            user_id, client_order_id, strategy_id, strategy_version, symbol,
            exchange, side, order_type, quantity, status, filled_quantity
        ) VALUES ($1, $2, 'fa3-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                  'MARKET', 1, 'CREATED', 0)
        RETURNING order_id
        """,
        user_id,
        f"fa3-test-{uuid4().hex}",
    )


async def test_upgrade_downgrade_upgrade_round_trip_adds_and_removes_columns(pool):
    for table in ("orders", "fills"):
        for column in ("fund_id", "portfolio_id"):
            assert await _column_exists(pool, table, column)

    _run_alembic("downgrade", _DOWN_REVISION)
    for table in ("orders", "fills"):
        for column in ("fund_id", "portfolio_id"):
            assert not await _column_exists(pool, table, column)

    _run_alembic("upgrade", "head")
    for table in ("orders", "fills"):
        for column in ("fund_id", "portfolio_id"):
            assert await _column_exists(pool, table, column)


async def test_negative_insert_with_nonexistent_fund_id_rejected_by_fk(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO orders (
                    user_id, client_order_id, strategy_id, strategy_version, symbol,
                    exchange, side, order_type, quantity, status, filled_quantity, fund_id
                ) VALUES ($1, $2, 'fa3-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                          'MARKET', 1, 'CREATED', 0, $3)
                """,
                user_id,
                f"fa3-test-{uuid4().hex}",
                uuid4(),
            )


async def test_fills_are_never_backfilled_because_worm_blocks_update(pool):
    """`fills`는 `073beca589d5`의 append-only 트리거가 모든 UPDATE를 거부한다
    — 부모 order가 정상 백필돼도 fill 행은 영구히 NULL로 남는 게 정답이다."""
    bootstrapped_user = await create_test_tenant(pool)
    repo = PostgresEntityRepository(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=default_entity_id(bootstrapped_user),
            tenant_id=bootstrapped_user,
            name="FA-3 Test Entity (fills)",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=default_fund_id(bootstrapped_user),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    await repo.create_portfolio(
        Portfolio(
            portfolio_id=default_portfolio_id(bootstrapped_user),
            fund_id=fund.fund_id,
            venue_account_ref="fa3-test-venue-fills",
        )
    )

    _run_alembic("downgrade", _DOWN_REVISION)
    async with pool.acquire() as conn:
        order_id = await _insert_bare_order(conn, bootstrapped_user)
        fill_id = await conn.fetchval(
            """
            INSERT INTO fills (
                provider_fill_id, venue, order_id, exchange_order_id, symbol, side,
                quantity, price, fee, fee_currency, liquidity, venue_ts
            ) VALUES ($1, 'bitget', $2, 'ext-2', 'BTC/USDT', 'BUY', 1, 10000, 1,
                      'USDT', 'TAKER', now())
            RETURNING id
            """,
            f"fa3-fill-{uuid4().hex}",
            order_id,
        )

    _run_alembic("upgrade", "head")

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1", order_id
        )
        fill_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM fills WHERE id = $1", fill_id
        )

    assert order_row["fund_id"] == default_fund_id(bootstrapped_user)
    assert fill_row["fund_id"] is None
    assert fill_row["portfolio_id"] is None


async def test_backfill_computes_default_ids_for_bootstrapped_user_and_nulls_the_rest(pool):
    bootstrapped_user = await create_test_tenant(pool)
    bare_user = await create_test_tenant(pool)

    repo = PostgresEntityRepository(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=default_entity_id(bootstrapped_user),
            tenant_id=bootstrapped_user,
            name="FA-3 Test Entity",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=default_fund_id(bootstrapped_user),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    await repo.create_portfolio(
        Portfolio(
            portfolio_id=default_portfolio_id(bootstrapped_user),
            fund_id=fund.fund_id,
            venue_account_ref="fa3-test-venue",
        )
    )

    _run_alembic("downgrade", _DOWN_REVISION)
    async with pool.acquire() as conn:
        bootstrapped_order_id = await _insert_bare_order(conn, bootstrapped_user)
        bare_order_id = await _insert_bare_order(conn, bare_user)

    _run_alembic("upgrade", "head")

    async with pool.acquire() as conn:
        bootstrapped_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1",
            bootstrapped_order_id,
        )
        bare_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1", bare_order_id
        )
        backfilled_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE order_id = ANY($1::uuid[]) AND fund_id IS NOT NULL",
            [bootstrapped_order_id, bare_order_id],
        )
        null_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE order_id = ANY($1::uuid[]) AND fund_id IS NULL",
            [bootstrapped_order_id, bare_order_id],
        )

    assert bootstrapped_row["fund_id"] == default_fund_id(bootstrapped_user)
    assert bootstrapped_row["portfolio_id"] == default_portfolio_id(bootstrapped_user)
    assert bare_row["fund_id"] is None
    assert bare_row["portfolio_id"] is None
    assert backfilled_count == 1
    assert null_count == 1
