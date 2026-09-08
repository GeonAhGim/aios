"""task-2091 — 리뷰 task-2083 REJECT #1 수정 검증: 동시 동일이메일 가입.

`AuthService.signup`(auth_service.py)은 `SELECT ... WHERE email = $1`로 중복을
먼저 확인하지만, 두 트랜잭션이 그 확인을 동시에 통과하면(TOCTOU) 진 쪽의
`INSERT INTO users`가 `users_email_key` UniqueViolationError를 그대로
던져 500이 났다(실DB로 재현 확인됨, task-2083). 지금은 그 위반만 골라
`ConcurrencyConflictError`(§3.3 STATE_CONCURRENCY_CONFLICT/409)로 바꾼다.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await retry_too_many_connections(
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    )
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _register(client: AsyncClient, email: str):
    return await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )


async def test_concurrent_signup_same_email_is_one_201_one_409_never_500(client, pool):
    email = f"test-{uuid.uuid4().hex}@example.com"

    first, second = await asyncio.gather(
        _register(client, email), _register(client, email)
    )
    responses = [first, second]

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 409], [(r.status_code, r.text) for r in responses]

    loser = first if first.status_code == 409 else second
    assert loser.json()["error_code"] == "STATE_CONCURRENCY_CONFLICT", loser.text

    async with pool.acquire() as conn:
        user_rows = await conn.fetch(
            "SELECT user_id FROM users WHERE email = $1", email
        )
        tenant_count = await conn.fetchval(
            "SELECT count(*) FROM tenant t JOIN users u ON u.user_id = t.id "
            "WHERE u.email = $1 AND t.kind = 'PERSONAL'",
            email,
        )
    assert len(user_rows) == 1, "정확히 한 건만 커밋돼야 한다(패배한 트랜잭션은 롤백)."
    assert tenant_count == 1, "승자의 personal tenant 행은 그대로 남아야 한다."


async def test_concurrent_signup_different_emails_both_succeed(client):
    """대조군 — 경합 처리가 서로 다른 이메일의 정상 동시가입까지 막지 않는지 확인."""
    email_a = f"test-{uuid.uuid4().hex}@example.com"
    email_b = f"test-{uuid.uuid4().hex}@example.com"

    responses = await asyncio.gather(
        _register(client, email_a), _register(client, email_b)
    )

    assert [r.status_code for r in responses] == [201, 201], [
        (r.status_code, r.text) for r in responses
    ]
