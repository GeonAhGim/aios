"""task-2020 — PLT-26/PLT-28 원자성 적대적 테스트.

`AuthService.signup`은 `users` 행과 그 PERSONAL `tenant` 행(id == user_id)을
같은 트랜잭션에 넣는다(auth_service.py). 이 테스트는 두 번째 INSERT(tenant)가
실패하면 첫 번째 INSERT(users)도 롤백되는지 — 즉 정말 "같은 트랜잭션"인지를
증명한다.

`users.user_id`는 서버가 임의로 생성하므로(과거엔 `DEFAULT gen_random_uuid()`
의존, 지금은 `AuthService.signup`이 `uuid4()`로 미리 뽑아 명시적으로 INSERT)
가입 전에 그 값을 미리 알 수 없다 — 그래서 `uuid4`를 monkeypatch해 값을
고정하고, 그 id로 `tenant` 행을 먼저 선점해 충돌을 강제한다(이미 이 id를
쓰는 다른 프로세스/오염된 상태를 흉내).
"""
from __future__ import annotations

import uuid
from uuid import UUID

import asyncpg
import pytest

from src.foundation.trust.domain.models import TenantKind
from src.services import auth_service as auth_service_module
from src.services.auth_service import AuthService

JWT_SECRET = "test-secret-key-not-for-production"
STRONG_PASSWORD = "Str0ng!Passw0rd"


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def auth(pool):
    return AuthService(pool, jwt_secret_key=JWT_SECRET)


async def test_signup_rolls_back_user_row_when_tenant_insert_conflicts(
    auth, pool, monkeypatch
):
    fixed_user_id = uuid.uuid4()
    monkeypatch.setattr(auth_service_module, "uuid4", lambda: fixed_user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenant (id, kind) VALUES ($1, $2)",
            fixed_user_id,
            TenantKind.PERSONAL.value,
        )

    email = _unique_email()
    with pytest.raises(asyncpg.UniqueViolationError):
        await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM users WHERE email = $1", email
        )
    assert count == 0


async def test_signup_creates_matching_user_and_tenant_id(auth, pool):
    """정상 경로 대조군 — 충돌이 없으면 users.user_id == tenant.id로 둘 다 남는다."""
    email = _unique_email()
    user = await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        tenant_id: UUID | None = await conn.fetchval(
            "SELECT id FROM tenant WHERE id = $1 AND kind = 'PERSONAL'", user.user_id
        )
        user_count = await conn.fetchval(
            "SELECT count(*) FROM users WHERE user_id = $1", user.user_id
        )
    assert tenant_id == user.user_id
    assert user_count == 1
