"""PLT-26 M4 마이그레이션(`tenant`, `tenant_membership`) 실DB 통합테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 DDL, §9 PLT-26
DoD("alembic upgrade head && downgrade -1 && upgrade head 왕복" +
"personal tenant backfill(id=user_id)과 부분 UNIQUE 제약이 실DB에서 검증").

`tests/integration/conftest.py`가 import 시점에 `DATABASE_URL`을
`TEST_DATABASE_URL`로 고정하므로(§ tests bootstrap), 여기서 띄우는 `alembic`
서브프로세스도 기본적으로 같은 값을 물려받아 이 세션 전용 테스트 DB에
접속한다 -- 단, 실제 downgrade/upgrade 왕복을 도는
`test_downgrade_then_upgrade_backfills_personal_tenant`는 task-5795 fix로
`tests/support/db.ensure_worker_database`가 복제한 일회용 DB에서만 돈다
(중단돼도 공유 세션 DB를 손상시키지 않기 위함 -- 아래 해당 테스트
docstring 참고).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from tests.integration.conftest import create_test_user
from tests.support.db import ensure_worker_database
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ADR-2026-09-09-C §"축별 성능 예산" — tenant_membership 삽입 전용 항목은
# 없으므로, 가장 근접한 DB 쓰기 비교축(주문 제출->ACK p95 50ms, paper)을
# 고정비 예산으로 차용한다.
MEMBERSHIP_INSERT_P95_BUDGET_MS = 50.0


def _asyncpg_dsn() -> str:
    env = dotenv_values(_PROJECT_ROOT / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str, database_url: str | None = None) -> None:
    env = {**os.environ, "DATABASE_URL": database_url} if database_url else None
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
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head() -> Generator[None, None, None]:
    """각 테스트 시작 시 head 상태를 보장한다 — 정의 순서에 의존하지 않고,
    라운드트립 테스트가 assert 실패로 중단돼도 다음 테스트가 downgrade된
    스키마를 보지 않게 한다."""
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
    return reg is not None


async def test_downgrade_then_upgrade_backfills_personal_tenant() -> None:
    """task-5795 root-cause fix (same pattern as task-5783's
    test_db_transition_trigger.py fix): this used to run its `alembic
    downgrade 94124c286c10` / `upgrade head` round trip directly against the
    process-shared `DATABASE_URL` -- the same session-lifetime DB every
    other test and `scripts/replay_verify.py` reads. `94124c286c10` is
    upstream of `073beca589d5` (oms order_events creation), so this
    downgrade drops `order_events` too; if the pytest process is killed
    mid-test (local_ci step timeout) before the restoring "upgrade head"
    runs, the shared DB is permanently left without `order_events`,
    reproducing esc-ci-replay_verify's `UndefinedTableError`. Round trip now
    runs against its own disposable DB clone
    (`tests/support/db.ensure_worker_database`), so an interrupted downgrade
    can only corrupt its own throwaway DB."""
    migration_db_url = await ensure_worker_database(
        os.environ["DATABASE_URL"], "tenantmembershiprt"
    )
    migration_pool = await asyncpg.create_pool(
        migration_db_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1,
        max_size=2,
    )
    try:
        user_id = await create_test_user(migration_pool)

        await purge_position_snapshots(
            migration_pool
        )  # deep downgrade: see tests/support/deep_downgrade.py
        _run_alembic(
            "downgrade", "94124c286c10", database_url=migration_db_url
        )  # PLT-26의 down_revision — 이후 PLT-23(§9)이
        # head 위에 새 리비전을 쌓았으므로 상대 이동("-1")은 더 이상 tenant/
        # tenant_membership을 벗기지 못한다(그 대신 자기 자신의 새 head만 벗김).
        assert not await _table_exists(migration_pool, "tenant")
        assert not await _table_exists(migration_pool, "tenant_membership")

        _run_alembic("upgrade", "head", database_url=migration_db_url)
        assert await _table_exists(migration_pool, "tenant")
        assert await _table_exists(migration_pool, "tenant_membership")

        async with migration_pool.acquire() as conn:
            tenant_row = await conn.fetchrow(
                "SELECT kind, state FROM tenant WHERE id = $1", user_id
            )
            membership_rows = await conn.fetch(
                "SELECT role, state, revision FROM tenant_membership "
                "WHERE tenant_id = $1 AND subject_id = $1",
                user_id,
            )
    finally:
        await migration_pool.close()

    assert tenant_row is not None
    assert tenant_row["kind"] == "PERSONAL"
    assert tenant_row["state"] == "ACTIVE"
    assert len(membership_rows) == 1
    assert membership_rows[0]["role"] == "OWNER"
    assert membership_rows[0]["state"] == "ACTIVE"
    assert membership_rows[0]["revision"] == 1


async def test_active_membership_unique_per_tenant_subject(pool: asyncpg.Pool) -> None:
    subject_id = await create_test_user(pool)
    tenant_id = uuid4()  # ORGANIZATION tenant는 personal backfill과 무관한 신규 id

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)
        await conn.execute(
            "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
            "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
            tenant_id,
            subject_id,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'ADMIN', 'ACTIVE')",
                tenant_id,
                subject_id,
            )

    # 부분 인덱스(WHERE state = 'ACTIVE')라 비활성 상태는 같은 (tenant_id, subject_id)로
    # 공존할 수 있다 — SUSPENDED/REVOKED 이력 행은 막지 않는 것이 의도다.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
            "VALUES ($1, $2, 'ADMIN', 'SUSPENDED')",
            tenant_id,
            subject_id,
        )
        rows = await conn.fetch(
            "SELECT state FROM tenant_membership WHERE tenant_id = $1 AND subject_id = $2",
            tenant_id,
            subject_id,
        )
    assert {r["state"] for r in rows} == {"ACTIVE", "SUSPENDED"}


# ----------------------------------------------------------------------
# negative — CHECK/FK 제약 (unique violation과 별개의 위반 경로)
# ----------------------------------------------------------------------


async def test_tenant_kind_check_constraint_rejects_invalid_kind(pool: asyncpg.Pool) -> None:
    """negative — `tenant.kind`는 PERSONAL/HOUSEHOLD/ORGANIZATION 세 값만
    허용한다(f4a6b8c0d2e4 CHECK). 임의 문자열은 거부되어야 한다."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'INVALID_KIND')", uuid4())


async def test_tenant_membership_role_check_rejects_invalid_role(pool: asyncpg.Pool) -> None:
    """negative — `tenant_membership.role`은 OWNER/ADMIN/MEMBER/AUDITOR/
    SERVICE만 허용한다. 임의 문자열은 거부되어야 한다."""
    subject_id = await create_test_user(pool)
    tenant_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role) "
                "VALUES ($1, $2, 'ROOT')",
                tenant_id,
                subject_id,
            )


async def test_tenant_membership_subject_must_reference_existing_user(pool: asyncpg.Pool) -> None:
    """negative — `tenant_membership.subject_id`는 `users(user_id)` FK다.
    존재하지 않는 subject_id는 ForeignKeyViolation으로 거부되어야 한다."""
    tenant_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role) "
                "VALUES ($1, $2, 'MEMBER')",
                tenant_id,
                uuid4(),  # users 테이블에 없는 임의 id
            )


# ----------------------------------------------------------------------
# 실패 주입 — 원자적 tenant 생성이 부분 실패한 사용자를 backfill이 치유하는지
# ----------------------------------------------------------------------


async def test_backfill_heals_user_left_without_tenant_row(pool: asyncpg.Pool) -> None:
    """실패 주입 — b8ac30eb4fe0 docstring이 설명하는 실제 장애(task-2020
    이전에는 `users` insert와 `tenant` insert가 원자적이지 않아, 사용자는
    생겼는데 대응 tenant가 없는 상태가 남을 수 있었다)를 직접 재현한다.
    `create_test_user`는 (AuthService.signup이 아니라 raw INSERT라) 정확히
    이 "user는 있는데 tenant가 없는" 상태를 만든다 — 이 오염 상태에서
    b8ac30eb4fe0의 backfill SQL을 재실행해 (1) 치유되는지, (2) 두 번째
    실행은 0행이라 idempotent한지 증명한다. 실DB 제약 위반(FK/unique)만으로는
    이 복구 경로가 검증되지 않으므로 unique violation과는 다른 축이다."""
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT id FROM tenant WHERE id = $1", user_id) is None

        first_run = await conn.execute(
            "INSERT INTO tenant (id, kind) "
            "SELECT u.user_id, 'PERSONAL' FROM users u WHERE u.user_id = $1 "
            "AND NOT EXISTS (SELECT 1 FROM tenant t WHERE t.id = u.user_id)",
            user_id,
        )
        assert first_run == "INSERT 0 1"  # 오염된 행이 치유됨

        healed = await conn.fetchrow("SELECT id, kind FROM tenant WHERE id = $1", user_id)
        assert healed is not None
        assert healed["kind"] == "PERSONAL"

        second_run = await conn.execute(
            "INSERT INTO tenant (id, kind) "
            "SELECT u.user_id, 'PERSONAL' FROM users u WHERE u.user_id = $1 "
            "AND NOT EXISTS (SELECT 1 FROM tenant t WHERE t.id = u.user_id)",
            user_id,
        )
        assert second_run == "INSERT 0 0"  # 재실행은 idempotent — 중복 삽입 없음


# ----------------------------------------------------------------------
# 성능 단언 — tenant_membership 삽입 고정비 p95
# ----------------------------------------------------------------------


async def test_tenant_membership_insert_latency_p95_within_budget(pool: asyncpg.Pool) -> None:
    """수치 성능 단언 — 부분 UNIQUE 인덱스(uq_tenant_membership_active)가
    걸려 있는 상태에서 `tenant_membership` 삽입 1회의 p95 지연시간이
    예산을 넘지 않아야 한다. 실측 기준선은 로컬 DB에서 수 ms 수준이며,
    이 단언은 인덱스/트리거 회귀로 수십 ms 이상 느려지는 것을 잡기 위함이지
    현재 실측치에 딱 맞춘 타이트한 문턱이 아니다."""
    tenant_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)

    n = 30
    latencies: list[float] = []
    for _ in range(n):
        subject_id = await create_test_user(pool)
        async with pool.acquire() as conn:
            start = time.perf_counter()
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
                tenant_id,
                subject_id,
            )
            latencies.append(time.perf_counter() - start)
        # 다음 반복의 subject_id는 새 user라 부분 인덱스와 충돌하지 않는다 —
        # 측정 대상은 INSERT 고정비이지 unique 충돌 재시도가 아니다.

    latencies.sort()
    p95_ms = latencies[int(n * 0.95)] * 1000
    assert p95_ms < MEMBERSHIP_INSERT_P95_BUDGET_MS, (
        f"tenant_membership INSERT p95 지연시간 {p95_ms:.4f}ms가 예산 "
        f"{MEMBERSHIP_INSERT_P95_BUDGET_MS}ms 초과"
    )


# ----------------------------------------------------------------------
# 게이트 적색 재현 — 부분 UNIQUE 인덱스가 빠지면 기존 양성 단언이 실패해야 함
# ----------------------------------------------------------------------


async def test_gate_red_reproduction_without_partial_unique_index(pool: asyncpg.Pool) -> None:
    """게이트 적색 재현 — `uq_tenant_membership_active`가 부분 인덱스
    (`WHERE state = 'ACTIVE'`)가 아니라 일반 UNIQUE였다면(그럴듯한 회귀:
    누군가 "단순화"하며 WHERE 절을 지움), 현재
    `test_active_membership_unique_per_tenant_subject`가 성공을 기대하는
    "SUSPENDED 이력 행과의 공존" 삽입이 실제로 UniqueViolationError로
    적색화됨을 직접 재현한다. 실제 `tenant_membership`/`uq_tenant_membership_
    active`는 다른 테스트가 남긴 (같은 tenant_id, subject_id 쌍의) 합법적인
    ACTIVE+SUSPENDED 중복 행을 이미 갖고 있을 수 있어, 그 위에 전역 UNIQUE
    인덱스를 직접 만들면 이 테스트와 무관한 데이터와 충돌한다 — 그래서
    동일 DDL을 가진 트랜잭션 스코프 임시 테이블(`ON COMMIT DROP`)에서만
    재현하고, 실제 테이블/인덱스는 건드리지 않는다."""
    tenant_id = uuid4()
    subject_id = uuid4()

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE tmp_membership_regression "
            "(LIKE tenant_membership INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        await conn.execute(
            "INSERT INTO tmp_membership_regression (tenant_id, subject_id, role, state) "
            "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
            tenant_id,
            subject_id,
        )
        # 회귀 재현: WHERE 절 없는 일반 UNIQUE 인덱스
        await conn.execute(
            "CREATE UNIQUE INDEX ON tmp_membership_regression(tenant_id, subject_id)"
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO tmp_membership_regression (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'ADMIN', 'SUSPENDED')",
                tenant_id,
                subject_id,
            )
