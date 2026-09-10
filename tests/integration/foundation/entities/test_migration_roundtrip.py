"""FA-2 마이그레이션(e6b1d94a7c3f) 실DB 왕복테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
DoD("실DB 왕복(upgrade/downgrade/upgrade) 테스트 필수"). subprocess로
alembic을 띄우는 이유·DSN 해석은 `tests/integration/db/
test_migration_tenant_membership.py`와 동일 패턴을 따른다."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "c1f4a9e7b3d6"


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


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
    return reg is not None


async def test_upgrade_downgrade_upgrade_round_trip_recreates_all_four_tables(pool):
    for table in ("legal_entity", "fund", "portfolio", "sub_account"):
        assert await _table_exists(pool, table)

    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION)
    for table in ("legal_entity", "fund", "portfolio", "sub_account"):
        assert not await _table_exists(pool, table)

    _run_alembic("upgrade", "head")
    for table in ("legal_entity", "fund", "portfolio", "sub_account"):
        assert await _table_exists(pool, table)


async def _legal_entity_tenant_fk_target(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as conn:
        target = await conn.fetchval(
            """
            SELECT confrelid::regclass::text
            FROM pg_constraint
            WHERE conrelid = 'legal_entity'::regclass
              AND contype = 'f'
              AND conname = 'legal_entity_tenant_id_fkey'
            """
        )
    assert target is not None, "legal_entity_tenant_id_fkey가 존재하지 않는다"
    return str(target)


async def test_legal_entity_tenant_id_fk_targets_tenant_not_users(pool):
    # FA-2a(a0e7e1454b60) DoD — 교정 후 users를 FK하는 tenant_id가 0건.
    assert await _legal_entity_tenant_fk_target(pool) == "tenant"


async def test_fa2a_downgrade_restores_users_fk_then_upgrade_restores_tenant_fk(pool):
    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", "e6b1d94a7c3f")
    assert await _legal_entity_tenant_fk_target(pool) == "users"

    _run_alembic("upgrade", "head")
    assert await _legal_entity_tenant_fk_target(pool) == "tenant"
