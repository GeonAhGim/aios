"""task-2020 — PLT-26/PLT-28 P0 배선 증명.

`/auth/register`가 실제로 `AuthService.signup`을 태우고, `signup`이 실제로
`tenant` 행을 만드는지를 실 DB로 검증한다. `auth_service.py`의 tenant INSERT
한 줄을 지우면 이 테스트는 FAIL한다 — 이 테스트 자체가 그 배선증명이다
(`AuthService.signup`을 단위테스트로 모킹하면 배선이 끊겨도 통과해버린다).
"""
from __future__ import annotations

import os
import uuid
from uuid import UUID

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
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    )
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _register(client: AsyncClient) -> UUID:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return UUID(me.json()["data"]["user_id"])


async def test_signup_creates_personal_tenant_row(client, pool):
    user_id = await _register(client)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT kind FROM tenant WHERE id = $1", user_id
        )
    assert row is not None, "signup이 personal tenant 행을 만들지 않았다"
    assert row["kind"] == "PERSONAL"
