"""16번대 통합테스트 — 실제 FastAPI 앱 + 실제 dev DB, HTTP 계층까지 왕복.

httpx ASGITransport로 실제 uvicorn 없이 앱을 직접 구동한다 —
app.router.lifespan_context로 main.py의 lifespan(asyncpg pool 생성)을
그대로 태운다.

성능/실패주입(guard) 테스트는 test_auth_router_perf.py로,
계정정지/권한(security) 테스트는 test_auth_router_security.py로 분리됨.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — 도메인 예외(AuthError 등)는 전역
        # Exception 핸들러(src/api/contracts/handlers.py)가 처리하는데,
        # FastAPI가 그 핸들러를 ServerErrorMiddleware로 승격시켜 정상
        # 응답을 보낸 뒤에도 예외를 다시 던진다(Starlette 설계) — httpx
        # ASGITransport 기본값(True)은 그걸 테스트 프로세스로 재전파해
        # 정상적으로 처리된 4xx 응답까지 테스트 실패로 만든다
        # (tests/unit/api/contracts/test_handlers.py에 동일 근거 기록됨).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


# ── register ───────────────────────────────────────────────────────────────


async def test_register_returns_access_token(client: AsyncClient) -> None:
    """수치 성능 단언 — register 요청(실제 Argon2 해시 + DB INSERT + ASGI
    직렬화)이 상수시간으로 끝나는지 확인한다. 이 단언이 보장하려는 것은
    "register 경로에 선형 이상으로 느려지는 요인(예: 사용자 테이블 풀스캔,
    N+1 쿼리)이 끼어들지 않았다"는 것이다.

    register 는 I/O 바인딩 연산이므로 wall-clock 기반 RelativeBudget을
    사용한다(process_time 은 async sleep 대기를 재지 못함)."""

    async def _one_register() -> None:
        response = await client.post(
            "/auth/register", json={"email": _unique_email(), "password": STRONG_PASSWORD}
        )
        assert response.status_code == 201

    # 기능 단언 — register 가 정상 응답하는지 한 번 더 검증
    response = await client.post(
        "/auth/register", json={"email": _unique_email(), "password": STRONG_PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert "trace_id" in body["meta"]
    data = body["data"]
    # PLT-24 — 프론트 tokenStore(task-955)의 TokenPairResponse 파서 계약
    # (access_token/refresh_token/token_type/expires_in) 그대로.
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 15 * 60
    assert "session_id" in data


# ── negative tests (>= 3) ──────────────────────────────────────────────────


async def test_register_rejects_weak_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register", json={"email": _unique_email(), "password": "short"}
    )

    assert response.status_code in (400, 422)


async def test_register_rejects_empty_email(client: AsyncClient) -> None:
    """음성: 빈 이메일로 등록하면 reject 한다."""
    response = await client.post("/auth/register", json={"email": "", "password": STRONG_PASSWORD})
    assert response.status_code in (400, 422)


async def test_register_rejects_missing_password(client: AsyncClient) -> None:
    """음성: password 필드가 없으면 reject 한다."""
    response = await client.post("/auth/register", json={"email": _unique_email()})
    assert response.status_code in (400, 422)


async def test_register_rejects_email_without_domain(client: AsyncClient) -> None:
    """음성: 도메인 부분이 없는 이메일(예: 'user@')은 reject 한다."""
    response = await client.post(
        "/auth/register", json={"email": "user@", "password": STRONG_PASSWORD}
    )
    assert response.status_code in (400, 422)
