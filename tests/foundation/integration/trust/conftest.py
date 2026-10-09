from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.integration.conftest import create_test_tenant


def unique_purpose() -> str:
    """disclosure.purpose에는 UNIQUE(purpose, revision) 제약이 있어, 테스트 간
    격리를 위해 매번 새 purpose를 쓴다(다른 통합테스트의 `f"test-strategy-
    {uuid4().hex[:8]}"` 패턴과 동일 원칙)."""
    return f"test-purpose-{uuid4().hex[:8]}"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresTrustRepository:
    return PostgresTrustRepository(pool)


@pytest.fixture
def purpose() -> str:
    return unique_purpose()


async def make_tenant(pool: asyncpg.Pool) -> UUID:
    return await create_test_tenant(pool)


async def _run_as_aios_app(conn: asyncpg.Connection) -> None:
    """asyncpg pool `setup` hook: every connection handed out by this pool
    runs as the non-superuser `aios_app` role (task-9453 / F1) so RLS is
    actually enforced for the repo methods under test — the module-level
    `pool` fixture above connects as the migrator/owner account, which
    PostgreSQL never subjects to RLS regardless of GUC binding."""
    await conn.execute("SET ROLE aios_app")


@pytest.fixture
async def aios_app_pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2, setup=_run_as_aios_app)
    yield p
    await p.close()


@pytest.fixture
def aios_app_repo(aios_app_pool: asyncpg.Pool) -> PostgresTrustRepository:
    return PostgresTrustRepository(aios_app_pool)


async def create_disclosure(pool: asyncpg.Pool, *, purpose: str, revision: int = 1) -> UUID:
    """disclosure는 운영자가 발행하는 컨텐츠이고 FND-01 사용자 커맨드 범위 밖이라
    (71번 §6 엔드포인트 목록에 없음), 테스트는 이 헬퍼로 직접 행을 만든다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO disclosure (purpose, revision, content_hash) "
            "VALUES ($1, $2, $3) RETURNING id",
            purpose,
            revision,
            f"hash-{uuid4().hex[:8]}",
        )
    disclosure_id: UUID = row["id"]
    return disclosure_id


async def retire_disclosure(pool: asyncpg.Pool, disclosure_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE disclosure SET retired_at = now() WHERE id = $1", disclosure_id)
