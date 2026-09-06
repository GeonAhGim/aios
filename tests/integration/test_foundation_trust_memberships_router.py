"""PLT-29 통합테스트 — /v1/foundation/trust/memberships 라우터 등록 확인.

task-1722(P1-C) — 커맨드(grant/suspend/revoke_membership)는
tests/foundation/integration/trust/test_membership_admin.py가 이미 실DB로
검증한다. 이 파일은 라우터 자체가 앱에 배선됐는지(router_registry.py
누락으로 0 임포터였던 결함)만 HTTP 계층에서 확인한다."""
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_post_grant_membership_requires_authentication(client):
    response = await client.post(
        "/v1/foundation/trust/memberships",
        json={"subject_id": str(uuid.uuid4()), "role": "MEMBER"},
    )
    assert response.status_code == 401


async def test_post_grant_membership_without_mfa_step_up_is_403(client):
    """신규 가입 직후에는 mfa_verified=False다(grant_membership.py 가드) —
    라우터가 실제로 커맨드까지 호출을 관통시켜 MembershipMfaRequiredError를
    403 AUTH_MFA_REQUIRED로 매핑함을 확인한다(exception_registry_foundation.py
    PLT-29 항목이 실제로 이 경로에서 발동함을 증명)."""
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/trust/memberships",
        json={"subject_id": str(uuid.uuid4()), "role": "MEMBER"},
        headers=headers,
    )
    assert response.status_code == 403
