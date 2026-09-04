"""FND-02 통합테스트 — /v1/foundation/mandates 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import time
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
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
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — task-1108이 mandates 라우터의 raw
        # HTTPException을 도메인 예외로 교체했다. 도메인 예외는 이제 전역
        # Exception 핸들러(ServerErrorMiddleware 승격)를 거치는데, Starlette가
        # 정상 응답 뒤에도 예외를 재전파하기 때문에 필요하다(test_auth_router.py
        # client 픽스처와 동일 근거).
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


async def test_mandate_status_requires_authentication(client):
    response = await client.get("/v1/foundation/mandates/status")
    assert response.status_code == 401


async def test_mandate_status_starts_empty(client):
    headers = await _register(client)
    response = await client.get("/v1/foundation/mandates/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["active_revision"] is None


async def test_create_draft_then_activate_then_evaluate(client):
    headers = await _register(client)

    draft_response = await client.post(
        "/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers
    )
    assert draft_response.status_code == 201
    draft = draft_response.json()["data"]
    assert draft["state"] == "DRAFT"

    activate_response = await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate",
        json={},
        headers=headers,
    )
    assert activate_response.status_code == 200
    assert activate_response.json()["data"]["state"] == "ACTIVE"

    status_response = await client.get("/v1/foundation/mandates/status", headers=headers)
    assert status_response.json()["data"]["active_revision"]["id"] == draft["id"]

    evaluate_response = await client.post(
        "/v1/foundation/mandates/policy:evaluate",
        json={"command_type": "paper_deployment", "asset": "XYZ"},
        headers=headers,
    )
    assert evaluate_response.status_code == 200
    assert evaluate_response.json()["data"]["outcome"] == "DENY"
    assert "POLICY_FORBIDDEN_ASSET" in evaluate_response.json()["data"]["reason_codes"]


async def test_evaluate_policy_without_mandate_is_404(client):
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/mandates/policy:evaluate",
        json={"command_type": "x"},
        headers=headers,
    )
    assert response.status_code == 404


async def test_pause_invalidates_risk_gate_cache_via_api(client):
    """레드팀 지적(agent-platform-12) 회귀 — mandates 자신의 캐시(fingerprint에
    revision state 포함)뿐 아니라 risk_gate가 그 위에 얹은 별도 10초 캐시도
    pause 즉시 무효화돼야 한다. 먼저 risk_gate 평가를 한 번 호출해 그 캐시를
    데운 뒤 pause하고, 곧바로 다시 평가해 stale ALLOW가 아니라 PAUSE 계열
    outcome이 나오는지 확인한다."""
    headers = await _register(client)
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )

    warm_response = await client.post(
        "/v1/foundation/risk-gate/evaluate", json={"gate_kind": "DEPLOYMENT"}, headers=headers
    )
    assert warm_response.status_code == 200
    assert warm_response.json()["data"]["outcome"] == "ALLOW"

    await client.post("/v1/foundation/mandates/mandate:pause", json={}, headers=headers)

    after_pause_response = await client.post(
        "/v1/foundation/risk-gate/evaluate", json={"gate_kind": "DEPLOYMENT"}, headers=headers
    )
    assert after_pause_response.status_code == 200
    assert after_pause_response.json()["data"]["outcome"] != "ALLOW"


async def test_material_amendment_via_api_requires_password_reauth(client):
    headers = await _register(client)
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )

    amended = (
        await client.post(
            "/v1/foundation/mandates/amendments",
            json={**DEFAULT_RULES, "max_total_exposure_pct": 95.0},
            headers=headers,
        )
    ).json()["data"]
    assert amended["cooling_off_started_at"] is not None

    no_reauth_response = await client.post(
        f"/v1/foundation/mandates/revisions/{amended['id']}:activate",
        json={},
        headers=headers,
    )
    assert no_reauth_response.status_code == 403

    wrong_password_response = await client.post(
        f"/v1/foundation/mandates/revisions/{amended['id']}:activate",
        json={"password": "WrongPassword1!"},
        headers=headers,
    )
    assert wrong_password_response.status_code == 403


async def test_material_amendment_full_gate_flow_via_api(client, pool):
    headers = await _register(client)
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )

    amended = (
        await client.post(
            "/v1/foundation/mandates/amendments",
            json={**DEFAULT_RULES, "max_total_exposure_pct": 95.0},
            headers=headers,
        )
    ).json()["data"]

    # 재인증은 통과하지만 아직 Trust Core 동의가 없어 403이어야 한다.
    reauth_only_response = await client.post(
        f"/v1/foundation/mandates/revisions/{amended['id']}:activate",
        json={"password": STRONG_PASSWORD},
        headers=headers,
    )
    assert reauth_only_response.status_code == 403

    purpose_revision = int(time.time())  # disclosure.revision은 INT(int32) — ms 단위는 넘친다
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO disclosure (purpose, revision, content_hash) "
            "VALUES ('portfolio_mandate_material_change', $1, 'hash')",
            purpose_revision,
        )
    await client.post(
        "/v1/foundation/trust/consents",
        json={
            "purpose": "portfolio_mandate_material_change",
            "disclosure_revision": purpose_revision,
        },
        headers=headers,
    )

    # 동의는 했지만 cooling-off이 아직 안 지났다.
    cooling_off_response = await client.post(
        f"/v1/foundation/mandates/revisions/{amended['id']}:activate",
        json={"password": STRONG_PASSWORD},
        headers=headers,
    )
    assert cooling_off_response.status_code == 409

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mandate_revision SET cooling_off_started_at = now() - interval '120 seconds' "
            "WHERE id = $1",
            amended["id"],
        )

    final_response = await client.post(
        f"/v1/foundation/mandates/revisions/{amended['id']}:activate",
        json={"password": STRONG_PASSWORD},
        headers=headers,
    )
    assert final_response.status_code == 200
    assert final_response.json()["data"]["state"] == "ACTIVE"
