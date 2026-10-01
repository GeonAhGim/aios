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

import src.api.routers.foundation.trust_memberships as trust_memberships_router
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


async def test_post_grant_membership_with_invalid_role_is_400(client):
    """불변식 위반 — MembershipRole은 OWNER/ADMIN/MEMBER/AUDITOR/SERVICE
    5개뿐이다(domain/models.py). Pydantic이 임의 문자열을 도메인까지
    통과시키지 않고 요청 검증 단계(RequestValidationError → 400)에서
    거부해야 한다."""
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/trust/memberships",
        json={"subject_id": str(uuid.uuid4()), "role": "NOT_A_ROLE"},
        headers=headers,
    )
    assert response.status_code == 400


async def test_post_suspend_membership_nonexistent_subject_is_404(client):
    """불변식 위반 — suspend_membership.py의 SuspendTargetNotFoundError는
    subject에 ACTIVE 멤버십이 없으면(미존재 포함, 73 §8.3 404 isomorphism)
    404 RESOURCE_NOT_FOUND로 매핑돼야 한다. MFA 불필요 경로라 등록 직후
    호출로도 재현 가능하다."""
    headers = await _register(client)
    response = await client.post(
        f"/v1/foundation/trust/memberships/{uuid.uuid4()}:suspend",
        headers=headers,
    )
    assert response.status_code == 404


async def test_post_grant_membership_with_malformed_tenant_header_is_400(client):
    """불변식 위반 — get_tenant_context(foundation_deps.py)는 X-Tenant-Id
    헤더가 있으면 UUID 파싱을 시도하고, 실패하면 400
    VALIDATION_INVALID_FIELD로 거부해야 한다(멤버십 커맨드까지 관통시키지
    않음)."""
    headers = await _register(client)
    headers = {**headers, "X-Tenant-Id": "not-a-uuid"}
    response = await client.post(
        "/v1/foundation/trust/memberships",
        json={"subject_id": str(uuid.uuid4()), "role": "MEMBER"},
        headers=headers,
    )
    assert response.status_code == 400


async def test_post_suspend_membership_dependency_failure_returns_500_not_silent_success(
    client, monkeypatch
):
    """실패주입 — suspend_membership 커맨드가 예기치 못한 예외를 던지면
    라우터가 조용히 성공을 가장하거나 빈 응답을 돌려주지 않고 500으로
    표면화돼야 한다(fail-closed 기본, CLAUDE.md §3)."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated dependency failure")

    monkeypatch.setattr(trust_memberships_router, "suspend_membership", _boom)
    headers = await _register(client)
    response = await client.post(
        f"/v1/foundation/trust/memberships/{uuid.uuid4()}:suspend",
        headers=headers,
    )
    assert response.status_code == 500


@pytest.mark.perf
async def test_membership_grant_latency_within_budget(client, monkeypatch, perf_budget):
    """성능 단언 — grant_membership 라우터 호출의 E2E 지연 시간이
    예산 내여야 한다(100ms, 실DB 왕복·MFA 검증 포함 wall-clock 측정).
    task-7434 패턴: wall-clock 성능 테스트는 @pytest.mark.perf 필수
    (xdist 병렬 실행 시 코어 경합 방지)."""
    headers = await _register(client)
    subject_id = str(uuid.uuid4())

    sample = await perf_budget.sample_async(
        lambda: client.post(
            "/v1/foundation/trust/memberships",
            json={"subject_id": subject_id, "role": "MEMBER"},
            headers=headers,
        )
    )
    response = sample.result
    elapsed_ms = sample.wall_ms

    assert response.status_code == 403  # MFA 검증 필요
    assert elapsed_ms < 100, f"grant_membership latency {elapsed_ms:.1f}ms exceeded budget 100ms"
