"""11.6 통합테스트 — 실제 dev DB 대상.

완료조건 실증: RUNNING 실행 존재 시 탈퇴 차단, 없으면 PENDING_DELETION
전이. 유예기간 중 재로그인 시 탈퇴 자동취소는 AuthService(11.2)와의
연동으로 검증한다.
"""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.account_deletion_service import AccountDeletionError, AccountDeletionService
from src.services.auth_service import AuthService
from src.services.execution_service import ExecutionService

JWT_SECRET = "test-secret-key-not-for-production"
STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return AccountDeletionService(pool)


@pytest.fixture
def auth(pool):
    return AuthService(pool, jwt_secret_key=JWT_SECRET)


async def _signup(auth):
    email = f"test-{uuid4().hex}@example.com"
    user = await auth.signup(email, STRONG_PASSWORD)
    return user, email


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _create_running_execution(pool, user_id):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, 'bitget', $2, $2)",
            user_id,
            b"dummy",
        )
    execution_service = ExecutionService(pool, load_risk_policy())
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id


async def test_request_deletion_succeeds_without_running_execution(service, auth):
    user, _ = await _signup(auth)

    result = await service.request_deletion(user.user_id, STRONG_PASSWORD)

    assert result.status == "PENDING_DELETION"


async def test_request_deletion_rejects_wrong_password(service, auth):
    user, _ = await _signup(auth)

    with pytest.raises(AccountDeletionError):
        await service.request_deletion(user.user_id, "WrongPassword1!")


async def test_request_deletion_blocked_by_running_execution(service, auth, pool):
    user, _ = await _signup(auth)
    await _create_running_execution(pool, user.user_id)

    with pytest.raises(AccountDeletionError):
        await service.request_deletion(user.user_id, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM users WHERE user_id = $1", user.user_id
        )
    assert status == "ACTIVE"


async def test_request_deletion_sets_status_and_grace_period(service, auth, pool):
    user, _ = await _signup(auth)

    result = await service.request_deletion(user.user_id, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, deletion_requested_at FROM users WHERE user_id = $1", user.user_id
        )
    assert row["status"] == "PENDING_DELETION"
    assert row["deletion_requested_at"] is not None
    assert result.deletion_effective_at > row["deletion_requested_at"]


async def test_relogin_during_grace_period_cancels_deletion(service, auth, pool):
    user, email = await _signup(auth)
    await service.request_deletion(user.user_id, STRONG_PASSWORD)

    logged_in = await auth.authenticate(email, STRONG_PASSWORD)

    assert logged_in.status == "ACTIVE"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, deletion_requested_at FROM users WHERE user_id = $1", user.user_id
        )
    assert row["status"] == "ACTIVE"
    assert row["deletion_requested_at"] is None


async def test_request_deletion_rejects_nonexistent_user(service):
    import uuid

    with pytest.raises(AccountDeletionError):
        await service.request_deletion(uuid.uuid4(), STRONG_PASSWORD)
