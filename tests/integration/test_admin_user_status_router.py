"""task-6879 DEEPEN — PATCH /admin/users/{user_id}/status.

task-112(PLT 라우터 적용: auth/users/admin 응답을 ApiResponse) 대상 DEEPEN.
빈 body/잘못된 값/존재하지 않는 대상에 대해 UserAdminService.change_status()가
명시적으로 거부하는지 확인한다. ADMIN_SETTABLE_STATUSES=(ACTIVE, SUSPENDED)
외의 값은 UserAdminError(→400)로, 존재하지 않는 user_id는
UserAdminNotFoundError(→404)로 거부한다(src/services/user_admin_service.py).

독립 파일로 분리한 이유(CLAUDE.md §7 file policy) — test_admin_router.py와
test_auth_router.py 둘 다 이미 code-ratchets-baseline.json의 loc_over_500/800
관측치에 걸려 있어, 여기 더하면 새 위반 파일이 생겨 baseline을 올려야 한다.
이 엔드포인트 하나만 다루는 독립된 변경축이라 별도 파일이 자연스럽다.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.admin_deps import get_user_admin_service
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
        # raise_app_exceptions=False — tests/integration/test_auth_router.py의
        # client 픽스처 주석 참조(4xx로 정상 처리된 도메인 예외를 httpx가
        # 재전파하지 않도록 한다).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client: AsyncClient) -> tuple[dict[str, str], str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["data"]["user_id"]


async def _make_admin(pool: asyncpg.Pool, user_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE user_id = $1", uuid.UUID(user_id)
        )


async def test_change_status_rejects_empty_body(client: AsyncClient, pool: asyncpg.Pool) -> None:
    """불변식 위반 — body에 필수 필드 status가 없으면 no-op으로 넘어가지
    않고 pydantic 검증 실패가 400으로 거부되어야 한다(exception_mapping이
    RequestValidationError를 VALIDATION_INVALID_FIELD/400으로 감싼다)."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    response = await client.patch(
        f"/admin/users/{target_id}/status", json={}, headers=admin_headers
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_change_status_rejects_invalid_status_value(
    client: AsyncClient, pool: asyncpg.Pool
) -> None:
    """불변식 위반 — 운영자는 ACTIVE/SUSPENDED로만 바꿀 수 있다. DELETED
    같은 값은 FD-11.4 탈퇴 절차 전용이라 UserAdminError(400)로 거부된다."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    response = await client.patch(
        f"/admin/users/{target_id}/status",
        json={"status": "DELETED"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_change_status_rejects_nonexistent_user(
    client: AsyncClient, pool: asyncpg.Pool
) -> None:
    """불변식 위반 — 존재하지 않는 user_id는 404 RESOURCE_NOT_FOUND로
    거부되어야 한다(400과 구분되어야 프론트가 원인을 알 수 있음, QA
    task-1163)."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)

    response = await client.patch(
        f"/admin/users/{uuid.uuid4()}/status",
        json={"status": "SUSPENDED"},
        headers=admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_change_status_failure_injection_service_error(
    client: AsyncClient, pool: asyncpg.Pool
) -> None:
    """실패주입 — 서비스 계층에서 예기치 못한 예외가 나면 500
    INTERNAL_ERROR로 감싸져야 하고 trace_id가 유지되어야 한다."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    class _BoomUserAdminService:
        async def change_status(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("Injected user_admin_service failure")

    app.dependency_overrides[get_user_admin_service] = lambda: _BoomUserAdminService()
    try:
        response = await client.patch(
            f"/admin/users/{target_id}/status",
            json={"status": "SUSPENDED"},
            headers=admin_headers,
        )
    finally:
        app.dependency_overrides.pop(get_user_admin_service, None)

    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "trace_id" in body


async def test_change_status_rejects_non_admin(client: AsyncClient) -> None:
    """불변식 위반 — admin이 아닌 사용자는 자기 자신의 상태조차
    변경할 수 없다(get_current_admin 의존성이 라우터 진입 자체를 막는다)."""
    headers, user_id = await _register(client)

    response = await client.patch(
        f"/admin/users/{user_id}/status",
        json={"status": "SUSPENDED"},
        headers=headers,
    )

    assert response.status_code in (401, 403)
