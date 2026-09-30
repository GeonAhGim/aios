"""11.1 통합테스트 — users/user_approval_settings 마이그레이션 + 미뤄둔
user_id FK 전체 연결 검증. 실제 dev DB(alembic upgrade head 적용 후) 대상.
"""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import create_test_user


def _database_url() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url


def _asyncpg_dsn() -> str:
    return _database_url().replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def db_conn():
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def test_users_and_settings_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": ["users", "user_approval_settings"]},
    )
    found = {row[0] for row in result}
    assert found == {"users", "user_approval_settings"}


EXPECTED_FKS = {
    "fk_tasks_user",
    "fk_strategies_owner_user",
    "fk_orders_user",
    "fk_positions_user",
    "fk_reconciliation_events_user",
    "fk_audit_log_user",
    "fk_notifications_user",
    "fk_notification_preferences_user",
    "fk_approval_requests_user",
}


async def test_deferred_user_id_fks_are_all_wired(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE constraint_name = ANY(:names)"
        ),
        {"names": list(EXPECTED_FKS)},
    )
    found = {row[0] for row in result}
    assert found == EXPECTED_FKS


async def test_orphan_user_id_is_rejected_by_new_fk(pool):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO reconciliation_events "
                "(user_id, symbol, exchange, internal_value, external_value) "
                "VALUES (gen_random_uuid(), 'BTC/USDT', 'bitget', '{}'::jsonb, '{}'::jsonb)"
            )


async def test_mandatory_wait_seconds_floor_is_enforced(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO user_approval_settings (user_id, mandatory_wait_seconds) "
                "VALUES ($1, 30)",
                user_id,
            )


async def test_user_approval_settings_upsert_succeeds_for_real_user(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO user_approval_settings (user_id, mode, second_approver_contact) "
            "VALUES ($1, 'DUAL', 'backup@example.com') RETURNING mandatory_wait_seconds",
            user_id,
        )
    assert row["mandatory_wait_seconds"] == 60


async def test_duplicate_email_violates_unique_constraint(pool):
    email = f"dup-{uuid4().hex}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2)",
            email,
            "hash-1",
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO users (email, password_hash) VALUES ($1, $2)",
                email,
                "hash-2",
            )


async def test_user_approval_settings_rejects_invalid_mode(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO user_approval_settings (user_id, mode) VALUES ($1, 'INVALID')",
                user_id,
            )


async def test_create_test_user_propagates_pool_acquire_failure(pool, monkeypatch):
    """실패주입: pool.acquire()가 예외를 던지면 create_test_user는 이를 삼키지
    않고 그대로 전파해야 한다(fail-closed) — 조용히 None을 반환하는 등의
    실패 은폐는 호출자가 존재하지 않는 user_id로 이후 INSERT를 계속하게
    만들어 FK 무결성 검증을 무력화한다."""

    def _boom(self):
        raise RuntimeError("simulated pool exhaustion")

    monkeypatch.setattr(type(pool), "acquire", _boom)
    with pytest.raises(RuntimeError, match="simulated pool exhaustion"):
        await create_test_user(pool)
