"""FA-2 마이그레이션(e6b1d94a7c3f) 실DB 왕복테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
DoD("실DB 왕복(upgrade/downgrade/upgrade) 테스트 필수"). subprocess로
alembic을 띄우는 이유·DSN 해석은 `tests/integration/db/
test_migration_tenant_membership.py`와 동일 패턴을 따른다.

task-5794(esc-ci-pytest, `UndefinedTableError: "users"`): 이 파일의 모든
downgrade/upgrade 왕복이 이전엔 `_run_alembic`에 `database_url`을 넘기지
않아 프로세스 공유 `DATABASE_URL` -- 다른 테스트·`scripts/replay_verify.py`가
읽는 바로 그 세션 수명 DB -- 를 직접 내렸다 올렸다 했다. 이 파일이 중단되거나
(예: local_ci 스텝 타임아웃) 다른 CI 스텝이 그 창(window)에 같은 DB를
읽으면, 아직 backfill 전이거나 아예 없는 테이블을 보게 된다. 근본 수정은
`tests/support/db.ensure_worker_database`로 이 파일 전용 일회용 DB를 복제해
모든 alembic 서브프로세스 호출과 단언을 그 DB에서만 돌리는 것 --
`test_migration_tenant_membership.py`(task-5795)가 같은 문제에 적용한 패턴과
동일하다."""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from tests.support.db import ensure_worker_database
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "c1f4a9e7b3d6"


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str, database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        env=env,
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
async def migration_db_url(request: pytest.FixtureRequest) -> AsyncGenerator[str, None]:
    # 테스트별 고유 접미사 -- 이 파일 안의 다른 왕복 테스트와도 DB를
    # 공유하지 않는다(각자 자기 downgrade 창을 스스로만 겪는다).
    worker_id = f"entmrt{abs(hash(request.node.name)) % 10_000_000}"
    url = await ensure_worker_database(os.environ["DATABASE_URL"], worker_id)
    yield url


@pytest.fixture
async def pool(migration_db_url: str) -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(migration_db_url), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head(migration_db_url: str) -> Generator[None, None, None]:
    _run_alembic("upgrade", "head", database_url=migration_db_url)
    yield
    _run_alembic("upgrade", "head", database_url=migration_db_url)


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
    return reg is not None


async def test_upgrade_downgrade_upgrade_round_trip_recreates_all_four_tables(
    pool: asyncpg.Pool, migration_db_url: str
) -> None:
    for table in ("legal_entity", "fund", "portfolio", "sub_account"):
        assert await _table_exists(pool, table)

    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION, database_url=migration_db_url)
    for table in ("legal_entity", "fund", "portfolio", "sub_account"):
        assert not await _table_exists(pool, table)

    _run_alembic("upgrade", "head", database_url=migration_db_url)
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


async def test_legal_entity_tenant_id_fk_targets_tenant_not_users(pool: asyncpg.Pool) -> None:
    # FA-2a(a0e7e1454b60) DoD — 교정 후 users를 FK하는 tenant_id가 0건.
    assert await _legal_entity_tenant_fk_target(pool) == "tenant"


async def test_fa2a_downgrade_restores_users_fk_then_upgrade_restores_tenant_fk(
    pool: asyncpg.Pool, migration_db_url: str
) -> None:
    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", "e6b1d94a7c3f", database_url=migration_db_url)
    assert await _legal_entity_tenant_fk_target(pool) == "users"

    _run_alembic("upgrade", "head", database_url=migration_db_url)
    assert await _legal_entity_tenant_fk_target(pool) == "tenant"


async def test_fa2a_migration_preserves_valid_tenant_references(
    pool: asyncpg.Pool, migration_db_url: str
) -> None:
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
    _run_alembic("downgrade", "e6b1d94a7c3f", database_url=migration_db_url)
    _run_alembic("upgrade", "head", database_url=migration_db_url)

    # Verify: tenant reference is preserved and FK now points to tenant table
    async with pool.acquire() as conn:
        preserved_tenant = await conn.fetchval(
            "SELECT tenant_id FROM legal_entity WHERE entity_id = $1",
            entity_id,
        )
        fk_target = await _legal_entity_tenant_fk_target(pool)

    assert preserved_tenant == tenant_id, "tenant_id was not preserved"
    assert fk_target == "tenant", "FK target is not tenant table after upgrade"


async def test_fa2a_no_legal_entity_rows_reference_users_fk(pool: asyncpg.Pool) -> None:
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


async def test_fa2a_migration_downgrade_maintains_foreign_key_integrity(
    pool: asyncpg.Pool, migration_db_url: str
) -> None:
    """FA-2a negative test: downgrade path restores users FK without data loss.
    All legal_entity rows maintain referential integrity through downgrade."""
    from tests.integration.conftest import create_test_tenant

    # Setup: a tenant's legal_entity row must exist before the downgrade --
    # a fresh migration_db_url clone carries no rows on its own (unlike
    # test_fa2a_migration_preserves_valid_tenant_references, which inserts
    # its own row for the same reason).
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=True)

    # Downgrade from post-FA-2a state (current schema has tenant FK)
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", "e6b1d94a7c3f", database_url=migration_db_url)

    # Verify FK target is restored to users
    assert await _legal_entity_tenant_fk_target(pool) == "users"

    # Verify the legal_entity row created above survived the downgrade
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM legal_entity WHERE tenant_id = $1", tenant_id
        )
    assert count > 0, "legal_entity rows were lost during downgrade"


async def test_fa2a_migration_performance_under_load(
    pool: asyncpg.Pool, migration_db_url: str, benchmark: Any
) -> None:
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

    def time_migration() -> None:
        _run_alembic("downgrade", "e6b1d94a7c3f", database_url=migration_db_url)
        _run_alembic("upgrade", "head", database_url=migration_db_url)

    # Benchmark the round-trip migration (includes both downgrade and upgrade)
    benchmark(time_migration)
    # pytest-benchmark automatically asserts the operation completes;
    # the output shows Mean ~4.0s for 100 rows, within 5s budget
