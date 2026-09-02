"""105번(동시성/원자성 표준) 통합테스트 — 실제 dev DB 대상.

users.status 컬럼을 대상으로 실제 조건부 UPDATE를 검증한다 — 별도 스크래치
테이블을 만들지 않고 이미 존재하는 테이블로 헬퍼 자체의 동작을 확인한다.
"""
import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def test_matching_expected_state_updates_and_returns_row(pool):
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        row = await conditional_update(
            conn,
            table="users",
            id_column="user_id",
            id_value=user_id,
            expected_state_column="status",
            expected_state_value="ACTIVE",
            set_values={"status": "SUSPENDED"},
            returning="status",
        )

    assert row["status"] == "SUSPENDED"
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM users WHERE user_id = $1", user_id)
    assert status == "SUSPENDED"


async def test_mismatched_expected_state_raises_and_does_not_write(pool):
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await conditional_update(
                conn,
                table="users",
                id_column="user_id",
                id_value=user_id,
                expected_state_column="status",
                expected_state_value="SUSPENDED",  # 실제로는 ACTIVE
                set_values={"status": "DELETED"},
            )

    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM users WHERE user_id = $1", user_id)
    assert status == "ACTIVE"


async def test_concurrent_conditional_updates_only_one_succeeds(pool):
    """105번 §4 형태 A — 실제 경합을 asyncio.gather로 재현."""
    user_id = await create_test_user(pool)

    async def attempt(new_status: str) -> None:
        async with pool.acquire() as conn:
            await conditional_update(
                conn,
                table="users",
                id_column="user_id",
                id_value=user_id,
                expected_state_column="status",
                expected_state_value="ACTIVE",
                set_values={"status": new_status},
            )

    results = await asyncio.gather(
        attempt("SUSPENDED"), attempt("PENDING_DELETION"), return_exceptions=True
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1


async def test_set_values_column_order_does_not_affect_binding(pool):
    """set_values 딕셔너리의 키 순서가 바뀌어도 값이 컬럼과 올바르게
    매칭되는지 확인 — $N 번호를 호출자가 손으로 세지 않는다는 게 이
    헬퍼의 핵심 안전장치(105번 §3)."""
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        await conditional_update(
            conn,
            table="users",
            id_column="user_id",
            id_value=user_id,
            expected_state_column="status",
            expected_state_value="ACTIVE",
            set_values={"status": "SUSPENDED", "display_name": "renamed"},
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, display_name FROM users WHERE user_id = $1", user_id
        )
    assert row["status"] == "SUSPENDED"
    assert row["display_name"] == "renamed"
