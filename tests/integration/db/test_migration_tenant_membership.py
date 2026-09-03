"""PLT-26 M4 마이그레이션(`tenant`, `tenant_membership`) 실DB 통합테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 DDL, §9 PLT-26
DoD("alembic upgrade head && downgrade -1 && upgrade head 왕복" +
"personal tenant backfill(id=user_id)과 부분 UNIQUE 제약이 실DB에서 검증").

`tests/integration/conftest.py`가 import 시점에 `DATABASE_URL`을
`TEST_DATABASE_URL`로 고정하므로(§ tests bootstrap), 여기서 띄우는 `alembic`
서브프로세스도 같은 값을 물려받아 이 세션 전용 테스트 DB에만 접속한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from tests.integration.conftest import create_test_user

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
