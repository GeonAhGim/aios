"""L4_compliance_and_regulatory_v1.0.md#9 CM-17 통합테스트 —
/v1/foundation/compliance 라우터. 실제 FastAPI 앱 + TEST_DATABASE_URL.

`tests/integration/test_foundation_mandates_router.py`와 같은 client/
_register 패턴을 쓴다 — mandate 초안/활성화/policy:evaluate로 실제
`policy_decision` 행을 만든 뒤(별도 "decision 생성" API가 없으므로 이
경로가 유일한 시딩 수단), 그 decision_id로 이 라우터의 조회·설명
엔드포인트를 검증한다.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"

DEFAULT_RULES = {
    "max_total_exposure_pct": 80.0,
    "max_single_instrument_pct": 20.0,
    "min_cash_buffer_pct": 5.0,
    "max_daily_loss_pct": 3.0,
    "allowed_autonomy": "PAPER",
    "forbidden_assets": ["XYZ"],
}


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False -- 도메인 예외가 전역 Exception
        # 핸들러(ServerErrorMiddleware 승격)를 거치는데, Starlette가 정상
        # 응답 뒤에도 예외를 재전파하기 때문에 필요하다
        # (test_foundation_mandates_router.py client 픽스처와 동일 근거).
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


async def _seed_denied_decision(client, headers: dict) -> str:
    """`forbidden_assets=["XYZ"]` 위임장을 활성화하고 그 자산으로
    policy:evaluate를 호출해 DENY `policy_decision` 한 건을 실제로 적재한다."""
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )
    evaluate_response = await client.post(
        "/v1/foundation/mandates/policy:evaluate",
        json={"command_type": "paper_deployment", "asset": "XYZ"},
        headers=headers,
    )
    assert evaluate_response.status_code == 200
    return evaluate_response.json()["data"]["id"]


async def test_mandate_status_requires_authentication(client):
    response = await client.get("/v1/foundation/compliance/mandate/status")
    assert response.status_code == 401


async def test_mandate_status_starts_empty(client):
    headers = await _register(client)
    response = await client.get("/v1/foundation/compliance/mandate/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["active_revision"] is None


async def test_get_decision_requires_authentication(client):
    response = await client.get(f"/v1/foundation/compliance/decisions/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_get_decision_returns_explained_decision(client):
    headers = await _register(client)
    decision_id = await _seed_denied_decision(client, headers)

    response = await client.get(
        f"/v1/foundation/compliance/decisions/{decision_id}", headers=headers
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["decision_id"] == decision_id
    assert data["verdict"] == "DENY"
    assert data["rule_hits"][0]["rule_id"] == "POLICY_FORBIDDEN_ASSET"


async def test_get_decision_is_reproducible_across_calls(client):
    """CM-13 explain() DoD (a) — 같은 decision_id를 두 번 조회하면 바이트
    단위로 같은 응답을 재현한다."""
    headers = await _register(client)
    decision_id = await _seed_denied_decision(client, headers)

    first = await client.get(f"/v1/foundation/compliance/decisions/{decision_id}", headers=headers)
    second = await client.get(f"/v1/foundation/compliance/decisions/{decision_id}", headers=headers)

    assert first.json()["data"] == second.json()["data"]


async def test_get_decision_unknown_id_is_404(client):
    headers = await _register(client)
    response = await client.get(
        f"/v1/foundation/compliance/decisions/{uuid.uuid4()}", headers=headers
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_get_decision_cross_tenant_access_is_same_404(client):
    """CM-17 DoD "404 동형" — 다른 tenant의 decision_id는 존재하지 않는
    id와 같은 상태코드·error_code로만 거부되어 존재 여부를 흘리지 않는다."""
    owner_headers = await _register(client)
    decision_id = await _seed_denied_decision(client, owner_headers)

    stranger_headers = await _register(client)
    response = await client.get(
        f"/v1/foundation/compliance/decisions/{decision_id}", headers=stranger_headers
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"
