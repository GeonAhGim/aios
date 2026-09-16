"""PLT-26 M4 마이그레이션(`tenant`, `tenant_membership`) 실DB 통합테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 DDL, §9 PLT-26
DoD("alembic upgrade head && downgrade -1 && upgrade head 왕복" +
"personal tenant backfill(id=user_id)과 부분 UNIQUE 제약이 실DB에서 검증").

`tests/integration/conftest.py`가 import 시점에 `DATABASE_URL`을
`TEST_DATABASE_URL`로 고정하므로(§ tests bootstrap), 여기서 띄우는 `alembic`
서브프로세스도 같은 값을 물려받아 이 세션 전용 테스트 DB에만 접속한다.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from tests.integration.conftest import create_test_user
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ADR-2026-09-09-C Decision 1 축별 성능 예산표에 멤버십 단일 INSERT 전용 항목이
# 없어 가장 가까운 유사 항목("주문 제출->ACK p95 50ms(paper)" — 동일하게 실DB
# 단일 행 쓰기 1회 왕복)을 자체 예산으로 차용한다.
_INSERT_P95_BUDGET_SECONDS = 0.05


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _asyncpg_dsn() -> str:
    env = dotenv_values(_PROJECT_ROOT / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


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


async def test_downgrade_then_upgrade_backfills_personal_tenant(pool):
    user_id = await create_test_user(pool)

    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", "94124c286c10")  # PLT-26의 down_revision — 이후 PLT-23(§9)이
    # head 위에 새 리비전을 쌓았으므로 상대 이동("-1")은 더 이상 tenant/
    # tenant_membership을 벗기지 못한다(그 대신 자기 자신의 새 head만 벗김).
    assert not await _table_exists(pool, "tenant")
    assert not await _table_exists(pool, "tenant_membership")

    _run_alembic("upgrade", "head")
    assert await _table_exists(pool, "tenant")
    assert await _table_exists(pool, "tenant_membership")

    async with pool.acquire() as conn:
        tenant_row = await conn.fetchrow("SELECT kind, state FROM tenant WHERE id = $1", user_id)
        membership_rows = await conn.fetch(
            "SELECT role, state, revision FROM tenant_membership "
            "WHERE tenant_id = $1 AND subject_id = $1",
            user_id,
        )

    assert tenant_row is not None
    assert tenant_row["kind"] == "PERSONAL"
    assert tenant_row["state"] == "ACTIVE"
    assert len(membership_rows) == 1
    assert membership_rows[0]["role"] == "OWNER"
    assert membership_rows[0]["state"] == "ACTIVE"
    assert membership_rows[0]["revision"] == 1


async def test_active_membership_unique_per_tenant_subject(pool):
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


async def test_insert_membership_rejects_invalid_role(pool):
    """CHECK(role IN (...)) — DDL이 나열한 5개 역할 밖의 값은 앱 계층 검증 없이도
    실DB 제약만으로 거부돼야 한다(fail-closed 최후 방어선)."""
    tenant_id = uuid4()
    subject_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                # VARCHAR(8) 안에 들어가되(길이 위반과 섞이지 않도록) 5개 허용값
                # 밖인 값을 골라 CHECK 제약 자체를 겨냥한다.
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'GHOST', 'ACTIVE')",
                tenant_id,
                subject_id,
            )


async def test_insert_membership_rejects_unknown_tenant(pool):
    """FK(tenant_id) — 존재하지 않는 tenant를 가리키는 멤버십은 거부돼야 한다.
    이 제약이 없으면 고아 멤버십 행이 조용히 남아 §8 tenant 격리 경계가
    무의미해진다."""
    subject_id = await create_test_user(pool)
    nonexistent_tenant_id = uuid4()

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
                nonexistent_tenant_id,
                subject_id,
            )


async def test_upgrade_fails_atomically_when_tenant_table_preexists(pool):
    """실패 주입 — 이전 마이그레이션 시도가 CREATE TABLE tenant까지만 어떤 이유로
    외부 커밋되고(예: 수동 hotfix, 다른 세션의 사고) 죽은 뒤 재기동한 상황을
    흉내낸다. env.py의 `context.begin_transaction()`(alembic/env.py 51-58행)이
    upgrade 전체를 단일 트랜잭션으로 묶으므로, CREATE TABLE tenant 충돌로 실패하면
    이어지는 CREATE TABLE tenant_membership과 backfill INSERT는 전혀 커밋되면 안
    된다 — 부분 커밋이 남으면 재시도 시 이미 있는 tenant 행 때문에 그 사용자의
    tenant_membership backfill이 조용히 누락되는 회귀가 된다."""
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", "94124c286c10")
    assert not await _table_exists(pool, "tenant")

    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE tenant (id UUID PRIMARY KEY)")  # 잔존 스텁

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode != 0  # CREATE TABLE tenant 충돌로 실패해야 함

    assert not await _table_exists(pool, "tenant_membership")  # fail-closed: 뒷부분 미커밋
    async with pool.acquire() as conn:
        stub_row_count = await conn.fetchval("SELECT count(*) FROM tenant")
    assert stub_row_count == 0  # 스텁 테이블 자체는 살아있지만 backfill INSERT는 없었다

    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE tenant")
    _run_alembic("upgrade", "head")  # 정리 후 재시도는 정상적으로 head까지 올라가야 한다
    assert await _table_exists(pool, "tenant")
    assert await _table_exists(pool, "tenant_membership")


async def test_membership_insert_p95_latency_within_budget(pool):
    """ADR-2026-09-09-C 예산("주문 제출->ACK p95 50ms(paper)")을 tenant_membership
    단일 INSERT 왕복의 자체 예산으로 차용해 p95가 그 안에 드는지 단언한다."""
    tenant_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)

    samples: list[float] = []
    async with pool.acquire() as conn:
        for _ in range(20):
            subject_id = await create_test_user(pool)
            started = time.perf_counter()
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
                tenant_id,
                subject_id,
            )
            samples.append(time.perf_counter() - started)

    assert _p95(samples) < _INSERT_P95_BUDGET_SECONDS


async def test_gate_red_reproduction_membership_insert_p95_budget_catches_regression(pool):
    """위 p95 단언이 상시-녹색 tautology가 아님을 증명 — INSERT 왕복마다 예산의
    3배 지연을 주입하면 같은 p95 단언이 실제로 적색(AssertionError)이 되어야
    한다."""
    tenant_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id)

    samples: list[float] = []
    async with pool.acquire() as conn:
        for _ in range(5):
            subject_id = await create_test_user(pool)
            started = time.perf_counter()
            time.sleep(_INSERT_P95_BUDGET_SECONDS * 3)  # 회귀 주입
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'MEMBER', 'ACTIVE')",
                tenant_id,
                subject_id,
            )
            samples.append(time.perf_counter() - started)

    with pytest.raises(AssertionError):
        assert _p95(samples) < _INSERT_P95_BUDGET_SECONDS
