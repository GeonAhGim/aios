"""PLT-14 통합테스트 — `src/api/contracts/idempotency.py` digest 대조.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.7, §9 PLT-14

`tests/integration/test_idempotency.py`(15 §15.1, PLT-14 이전 구현)는 이
리프에서 손대지 않는다 — `core/idempotency.py`의 `tenant_id`/`digest`가
전부 optional이라 그 파일은 무수정으로 계속 통과해야 한다(DoD).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.contracts.handlers import install_exception_handlers
from src.api.contracts.idempotency import (
    HEADER_NAME,
    IdempotencyScope,
    require_idempotency_key,
    run_idempotent,
)
from src.api.deps import get_current_user
from src.services.auth_service import User


def _asyncpg_dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


def _fake_user() -> User:
    return User(
        user_id=uuid.uuid4(),
        email="idem-digest@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _make_app(pool: asyncpg.Pool, calls: list[int], user: User) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user

    @app.post("/purchase")
    async def purchase(
        payload: dict,
        scope: IdempotencyScope = Depends(require_idempotency_key),
    ) -> dict:
        async def compute() -> tuple[int, dict]:
            calls.append(1)
            return 201, {"id": len(calls)}

        status_code, body = await run_idempotent(pool, scope, compute)
        return {"status_code": status_code, "body": body}

    return app


def _key() -> str:
    return f"idem-{uuid.uuid4().hex}"


async def test_first_call_executes_compute_and_replay_returns_same_response(pool):
    calls: list[int] = []
    user = _fake_user()
    app = _make_app(pool, calls, user)
    header = _key()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: header}
        )
        second = await client.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: header}
        )

    assert first.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1  # 두 번째 호출은 재생 — compute() 재실행 안 됨


async def test_same_key_different_body_returns_409_integrity_conflict(pool):
    calls: list[int] = []
    user = _fake_user()
    app = _make_app(pool, calls, user)
    header = _key()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: header}
        )
        second = await client.post(
            "/purchase", json={"amount": "99.00"}, headers={HEADER_NAME: header}
        )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error_code"] == "INTEGRITY_IDEMPOTENCY_CONFLICT"
    assert len(calls) == 1  # 두 번째 요청은 compute() 근처도 못 감


async def test_missing_header_returns_400_validation_required(pool):
    calls: list[int] = []
    app = _make_app(pool, calls, _fake_user())

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/purchase", json={"amount": "10.00"})

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"


async def test_header_too_short_returns_400_validation_required(pool):
    calls: list[int] = []
    app = _make_app(pool, calls, _fake_user())

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: "short"}
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"


async def test_different_tenants_do_not_collide_on_same_header_key(pool):
    """storage_key에 tenant_id가 들어가므로, 다른 사용자가 우연히 같은
    헤더값을 보내도 서로 다른 응답을 각자 받는다(선행 결함 회귀 방지)."""
    calls_a: list[int] = []
    calls_b: list[int] = []
    header = _key()

    app_a = _make_app(pool, calls_a, _fake_user())
    app_b = _make_app(pool, calls_b, _fake_user())

    transport_a = ASGITransport(app=app_a)
    transport_b = ASGITransport(app=app_b)
    async with AsyncClient(transport=transport_a, base_url="http://test") as client_a:
        response_a = await client_a.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: header}
        )
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        response_b = await client_b.post(
            "/purchase", json={"amount": "10.00"}, headers={HEADER_NAME: header}
        )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert len(calls_a) == 1
    assert len(calls_b) == 1  # tenant_id가 달라 독립적으로 compute() 실행됨


async def test_purge_expired_removes_only_past_rows(pool):
    from src.core.idempotency import purge_expired

    live_key = f"purge-live-{uuid.uuid4().hex}"
    expired_key = f"purge-expired-{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO idempotency_keys (key, status_code, response_body, expires_at) "
            "VALUES ($1, 201, '{}'::jsonb, now() + interval '1 hour')",
            live_key,
        )
        await conn.execute(
            "INSERT INTO idempotency_keys (key, status_code, response_body, expires_at) "
            "VALUES ($1, 201, '{}'::jsonb, $2)",
            expired_key,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

    deleted = await purge_expired(pool)

    assert deleted >= 1
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT 1 FROM idempotency_keys WHERE key = $1", expired_key
        ) is None
        assert await conn.fetchval(
            "SELECT 1 FROM idempotency_keys WHERE key = $1", live_key
        ) == 1
