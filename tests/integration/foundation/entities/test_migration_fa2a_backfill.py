"""FA-2a DEEPEN(task-3007) — a0e7e1454b60 백필 UPDATE의 실동작 증빙.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2a
task-2724 DEPTH 감사(docs/audit/DEPTH_FA.md)가 task-1747을 D1로 매긴
근거: `test_migration_static_no_users_fk.py`(정적 스캐너) +
`test_migration_roundtrip.py`(스키마 FK 표적만 확인, 4함수) 뿐이고
negative test 0건, 실패주입 없음, 성능단언 없음.

이 파일은 마이그레이션 본문의 실제 UPDATE 문(`a0e7e1454b60:36-43`)이
`e6b1d94a7c3f`(FK -> users) 상태에서 직접 심어 둔 데이터에 대해 어떻게
동작하는지를 실DB로 검증한다. 검증은 head가 아니라 `a0e7e1454b60` 딱
한 리비전만 올려서 확인한다 — head까지 올리면 그 뒤의 다른 마이그레이션
(예: tenant 테이블을 재구성/재백필하는 이후 리비전)이 결과에 섞여
a0e7e1454b60 자체의 동작인지 구분할 수 없다:

  1. tenant_id가 여전히 유효한 행은 값이 바뀌지 않는다(대조군).
  2. 참조하던 tenant가 사라진 행(불변식 파손 주입)만 첫 번째 tenant로
     재매핑된다 — docstring에 적힌 fallback을 실행해 본 적이 한 번도
     없었다.
  3. 재매핑 후에는 같은 리비전이 새로 건 FK(tenant)가 옛 표적(이제는
     없는 tenant_id)을 다시 거부한다.
  4. tenant가 하나도 없으면 UPDATE는 조용히 no-op해야 한다(EXISTS
     가드 회귀 방지) — ALTER TABLE ADD CONSTRAINT까지 실행하면 orphan
     행 자체가 있으니 그 단계가 실패하는 게 정상이라 UPDATE만 따로
     떼어서 확인한다.
  5. 오염된 행 50개에 대한 백필이 예산(5초) 안에서 끝난다.

`e6b1d94a7c3f`까지 내려가는 것은 FA-4(`963d5f3cfb1b`) 아래이므로
`purge_position_snapshots`가 필요하다(tests/support/deep_downgrade.py).
매 테스트 뒤에는 autouse 픽스처가 다시 head까지 올려 다음 테스트/파일에
영향을 남기지 않는다.

task-5840 root-cause fix (esc-ci-pytest, same class of bug as task-5783's
test_db_transition_trigger.py): this file used to run its real `alembic
downgrade e6b1d94a7c3f` / `upgrade head` round trips directly against the
process's shared `DATABASE_URL` -- the same session-lifetime DB every other
integration/e2e test (e.g. test_kill_switch_blocks_submission.py) reads.
If the pytest process is interrupted mid-test (step timeout, growing suite
runtime), the round trip can be interrupted between the downgrade (which
drops `users` down to the `e6b1d94a7c3f` shape) and the restoring `upgrade
head` in this test's own body/fixture teardown, permanently leaving the
*shared* DB without the head-shape `users` table for the rest of the CI
run -- exactly esc-ci-pytest's `UndefinedTableError: "users"` in an
unrelated e2e test. Every downgrade/upgrade call in this file now runs
against its own disposable DB clone (`tests/support/db.ensure_worker_database`)
so an interrupted downgrade can never corrupt state anything else reads."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests._perf.relative_budget import RelativeBudget
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.support.db import ensure_worker_database
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_FA2_REVISION = "e6b1d94a7c3f"
_FA2A_REVISION = "a0e7e1454b60"
_PERF_ROW_COUNT = 50
# task-7674: the original absolute 5.0s budget compared `elapsed` to this
# host's clock/disk/Postgres-lock speed, not the backfill code -- it went
# red (24.41s observed) on a busy/shared host (many concurrent worktrees
# hitting the same local Postgres, cf. scripts/setup_test_db.py's
# _list_test_databases docstring) with nothing in the migration changed; a
# rerun on the same host passed at 24.14s total (elapsed inside budget).
# Same RelativeBudget fix as task-7631: express the budget as a multiple of
# a same-process pure-Python calibration loop. This op is a single
# alembic-subprocess + real-DB migration (can't cheaply repeat for a
# best-of-N -- each sample would re-seed 50 rows), so it uses a single
# wall-clock sample (n=1) rather than assert_within's default best-of-5.
# Ratio derivation: worst locally observed elapsed 24.41s against a ~77ms
# calibration (~317x); 700 keeps >2x headroom over that worst sample to
# absorb further shared-Postgres contention while still catching a real
# O(n) -> O(n^2) regression in the backfill.
_PERF_MAX_RATIO = 700.0

# a0e7e1454b60의 백필 UPDATE 본문 그대로(교정 대상 SQL의 사본).
_BACKFILL_UPDATE_SQL = """
    UPDATE legal_entity
    SET tenant_id = (SELECT id FROM tenant ORDER BY created_at LIMIT 1)
    WHERE tenant_id NOT IN (SELECT id FROM tenant)
    AND EXISTS (SELECT 1 FROM tenant)
    """


class _Rollback(Exception):
    """트랜잭션을 롤백시키기 위한 신호 전용 예외 — 테스트 실패가 아니다."""


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
    worker_id = f"fa2abf{abs(hash(request.node.name)) % 10_000_000}"
    url = await ensure_worker_database(os.environ["DATABASE_URL"], worker_id)
    yield url


@pytest.fixture
async def pool(migration_db_url: str) -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(migration_db_url), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head(migration_db_url: str) -> Iterator[None]:
    _run_alembic("upgrade", "head", database_url=migration_db_url)
    yield
    _run_alembic("upgrade", "head", database_url=migration_db_url)


async def _insert_legal_entity_at_fa2(pool: asyncpg.Pool, *, tenant_id: UUID) -> UUID:
    """`e6b1d94a7c3f` 상태 컬럼(entity_id/tenant_id/name/jurisdiction/region_tag)만 채운다."""
    entity_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO legal_entity (entity_id, tenant_id, name, jurisdiction, region_tag) "
            "VALUES ($1, $2, 'DEEPEN-3007 fixture', 'KR', 'kr-seoul')",
            entity_id,
            tenant_id,
        )
    return entity_id


async def _tenant_id_of(pool: asyncpg.Pool, entity_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT tenant_id FROM legal_entity WHERE entity_id = $1", entity_id
        )
    assert value is not None
    return UUID(str(value))


async def _first_tenant_id(pool: asyncpg.Pool) -> UUID | None:
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT id FROM tenant ORDER BY created_at LIMIT 1")
    return UUID(str(value)) if value is not None else None


async def test_valid_tenant_id_is_not_remapped_by_backfill(pool, migration_db_url: str):
    # 대조군 — 정상 행은 백필이 값을 건드리지 않는다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _FA2_REVISION, database_url=migration_db_url)
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    entity_id = await _insert_legal_entity_at_fa2(pool, tenant_id=tenant_id)

    _run_alembic("upgrade", _FA2A_REVISION, database_url=migration_db_url)

    assert await _tenant_id_of(pool, entity_id) == tenant_id


async def test_orphaned_tenant_id_is_remapped_to_first_tenant(pool, migration_db_url: str):
    # 실패주입 — 행이 가리키던 tenant가 사라진 뒤(불변식 파손) 백필이
    # docstring이 약속한 fallback(첫 번째 tenant)으로 재매핑하는지 확인한다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _FA2_REVISION, database_url=migration_db_url)
    doomed_tenant = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    entity_id = await _insert_legal_entity_at_fa2(pool, tenant_id=doomed_tenant)

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tenant WHERE id = $1", doomed_tenant)

    expected_fallback = await _first_tenant_id(pool)
    assert expected_fallback is not None, "재매핑 표적이 있어야 이 시나리오가 성립한다"

    _run_alembic("upgrade", _FA2A_REVISION, database_url=migration_db_url)

    assert await _tenant_id_of(pool, entity_id) == expected_fallback


async def test_remapped_row_still_rejects_the_now_deleted_tenant_id(pool, migration_db_url: str):
    # negative — 재매핑 이후에도 FK는 여전히 강제된다: 사라진 옛 tenant_id로
    # 되돌리려는 시도는 새 FK(tenant)가 거부해야 한다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _FA2_REVISION, database_url=migration_db_url)
    doomed_tenant = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    entity_id = await _insert_legal_entity_at_fa2(pool, tenant_id=doomed_tenant)

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tenant WHERE id = $1", doomed_tenant)

    _run_alembic("upgrade", _FA2A_REVISION, database_url=migration_db_url)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE legal_entity SET tenant_id = $1 WHERE entity_id = $2",
                doomed_tenant,
                entity_id,
            )


async def test_backfill_is_noop_when_no_tenant_rows_exist(pool, migration_db_url: str):
    # negative — EXISTS 가드 회귀 방지: tenant가 하나도 없으면 백필 UPDATE는
    # `tenant_id`를 NULL로 덮어써 NOT NULL 위반으로 죽어서는 안 되고, orphan
    # 값을 그대로 둬야 한다. ALTER TABLE ADD CONSTRAINT 단계까지 가면(orphan이
    # 있으니 그 자체로 실패하는 게 맞는 동작) UPDATE 자체의 결함과 구분이 안
    # 되므로, 마이그레이션 본문의 UPDATE만 떼어 내 트랜잭션으로 감싸고 굳이
    # 커밋하지 않는다(끝에서 `_Rollback`으로 되돌려 tenant/tenant_membership
    # 행을 그대로 복구).
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _FA2_REVISION, database_url=migration_db_url)
    orphan_user_id = await create_test_user(pool)
    entity_id = await _insert_legal_entity_at_fa2(pool, tenant_id=orphan_user_id)

    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # tenant_membership이 tenant를 FK로 잡고 있어 먼저 비워야 한다.
                await conn.execute("DELETE FROM tenant_membership")
                await conn.execute("DELETE FROM tenant")
                remaining = await conn.fetchval("SELECT count(*) FROM tenant")
                assert remaining == 0, "이 시나리오는 tenant가 전부 비어 있어야 성립한다"

                await conn.execute(_BACKFILL_UPDATE_SQL)

                tenant_id_during = await conn.fetchval(
                    "SELECT tenant_id FROM legal_entity WHERE entity_id = $1", entity_id
                )
                assert tenant_id_during == orphan_user_id

                raise _Rollback()
        except _Rollback:
            pass

    # 롤백으로 tenant/tenant_membership 행이 복구됐으니 원래 값도 그대로다.
    assert await _tenant_id_of(pool, entity_id) == orphan_user_id


@pytest.mark.perf
async def test_backfill_of_fifty_orphaned_rows_completes_within_budget(pool, migration_db_url: str):
    # 성능단언 — 오염된 행 다수에 대한 백필이 예산 안에서 끝나는지 확인한다
    # (감사가 지적한 "성능단언 없음" 공백). fallback 표적은 이 테스트가 만든
    # tenant가 아니라 DB 전체에서 가장 오래된 tenant일 수 있으므로(공유
    # TEST_DATABASE_URL에 과거 테스트가 남긴 행이 쌓여 있다) 미리 값을
    # 고정하지 않고 백필 직전에 동적으로 조회한다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _FA2_REVISION, database_url=migration_db_url)

    doomed_tenants = [
        await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
        for _ in range(_PERF_ROW_COUNT)
    ]
    entity_ids = [
        await _insert_legal_entity_at_fa2(pool, tenant_id=tenant_id) for tenant_id in doomed_tenants
    ]
    async with pool.acquire() as conn:
        await conn.executemany("DELETE FROM tenant WHERE id = $1", [(t,) for t in doomed_tenants])

    expected_fallback = await _first_tenant_id(pool)
    assert expected_fallback is not None, "재매핑 표적이 있어야 이 시나리오가 성립한다"

    budget = RelativeBudget()
    sample = budget.measure(
        lambda: _run_alembic("upgrade", _FA2A_REVISION, database_url=migration_db_url),
        mode="wall",
        n=1,
        warmup=0,
    )
    assert sample.ratio < _PERF_MAX_RATIO, (
        f"{_PERF_ROW_COUNT}개 orphan 백필: {budget.describe(sample, max_ratio=_PERF_MAX_RATIO)}"
    )
    for entity_id in entity_ids:
        assert await _tenant_id_of(pool, entity_id) == expected_fallback
