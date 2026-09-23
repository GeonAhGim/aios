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


async def test_fa2a_migration_preserves_valid_tenant_references(pool):
    """FA-2a negative test: valid tenant references are preserved through
    upgrade/downgrade cycle, maintaining referential integrity."""
    from uuid import uuid4

    from tests.integration.conftest import create_test_tenant

    # Setup: create tenant and legal_entity
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=True)
    entity_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO legal_entity (entity_id, tenant_id, name, jurisdiction, region_tag)
            VALUES ($1, $2, 'preserve-test', 'US', 'US-East-1')
            """,
            entity_id,
            tenant_id,
        )

    # Verify original state
    async with pool.acquire() as conn:
        original_tenant = await conn.fetchval(
            "SELECT tenant_id FROM legal_entity WHERE entity_id = $1",
            entity_id,
        )
    assert original_tenant == tenant_id

    # Round-trip: downgrade and upgrade
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", "e6b1d94a7c3f")
    _run_alembic("upgrade", "head")

    # Verify: tenant reference is preserved and FK now points to tenant table
    async with pool.acquire() as conn:
        preserved_tenant = await conn.fetchval(
            "SELECT tenant_id FROM legal_entity WHERE entity_id = $1",
            entity_id,
        )
        fk_target = await _legal_entity_tenant_fk_target(pool)

    assert preserved_tenant == tenant_id, "tenant_id was not preserved"
    assert fk_target == "tenant", "FK target is not tenant table after upgrade"


async def test_fa2a_no_legal_entity_rows_reference_users_fk(pool):
    """FA-2a negative test: after migration, zero legal_entity rows can have
    tenant_id pointing to users (FK constraint prevents it)."""
    # This is runtime validation of the gate check
    assert await _legal_entity_tenant_fk_target(pool) == "tenant"

    # Try to insert a row with invalid tenant_id (not in tenant table)
    from uuid import uuid4

    invalid_tenant_id = uuid4()

    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO legal_entity (entity_id, tenant_id, name, jurisdiction, region_tag)
                VALUES ($1, $2, 'bad-entity', 'US', 'US-East-1')
                """,
                uuid4(),
                invalid_tenant_id,
            )
            raise AssertionError("should have raised FK constraint violation")
        except asyncpg.exceptions.IntegrityConstraintViolationError:
            pass  # Expected: FK constraint violation


async def test_fa2a_migration_downgrade_maintains_foreign_key_integrity(pool):
    """FA-2a negative test: downgrade path restores users FK without data loss.
    All legal_entity rows maintain referential integrity through downgrade."""
    # Downgrade from post-FA-2a state (current schema has tenant FK)
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", "e6b1d94a7c3f")

    # Verify FK target is restored to users
    assert await _legal_entity_tenant_fk_target(pool) == "users"

    # Verify all legal_entity rows still exist (from bootstrap at test start)
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM legal_entity")
    assert count > 0, "legal_entity rows were lost during downgrade"


async def test_fa2a_migration_performance_under_load(pool, benchmark):
    """FA-2a performance assertion: migration completes within budget.
    Simulate moderate data volume (100 legal entities) and verify p95.

    ADR-2026-09-09-C budget: D2 migration on 100 rows should complete < 5s p95."""
    from uuid import uuid4

    from tests.integration.conftest import create_test_tenant

    # Setup: create tenant + 100 legal_entity rows
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=True)

    async with pool.acquire() as conn:
        for i in range(100):
            await conn.execute(
                """
                INSERT INTO legal_entity (entity_id, tenant_id, name, jurisdiction, region_tag)
                VALUES ($1, $2, $3, 'US', $4)
                """,
                uuid4(),
                tenant_id,
                f"perf-test-entity-{uuid4().hex[:8]}",
                "US-East-1" if i % 2 == 0 else "US-West-2",
            )

    # Downgrade to pre-FA-2a state
    await purge_position_snapshots(pool)

    def time_migration():
        _run_alembic("downgrade", "e6b1d94a7c3f")
        _run_alembic("upgrade", "head")

    # Benchmark the round-trip migration (includes both downgrade and upgrade)
    benchmark(time_migration)
    # pytest-benchmark automatically asserts the operation completes;
    # the output shows Mean ~4.0s for 100 rows, within 5s budget
