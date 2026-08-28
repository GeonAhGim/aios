"""15번대 통합테스트 — /users/me/risk-* 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


_STABLE_ANSWERS = {
    "years_of_experience": 0,
    "investable_ratio_pct": 5,
    "loss_tolerance_pct": 5,
    "investment_goal": "LONG_TERM_GROWTH",
    "liquidity_need": "WITHIN_1_YEAR",
}

_AGGRESSIVE_ANSWERS = {
    "years_of_experience": 15,
    "investable_ratio_pct": 80,
    "loss_tolerance_pct": 50,
    "investment_goal": "SHORT_TERM_PROFIT",
    "liquidity_need": "OVER_3_YEARS",
}


async def test_risk_profile_missing_before_assessment(client):
    headers = await _register(client)

    response = await client.get("/users/me/risk-profile", headers=headers)

    assert response.status_code == 404


async def test_submit_assessment_returns_risk_profile(client):
    headers = await _register(client)

    response = await client.post(
        "/users/me/risk-assessment", json=_STABLE_ANSWERS, headers=headers
    )

    assert response.status_code == 201
    assert response.json()["risk_profile"] == "안정형"


async def test_risk_profile_reflects_latest_assessment(client):
    headers = await _register(client)
    await client.post("/users/me/risk-assessment", json=_STABLE_ANSWERS, headers=headers)

    response = await client.get("/users/me/risk-profile", headers=headers)

    assert response.status_code == 200
    assert response.json()["risk_profile"] == "안정형"


async def test_reassessment_flags_higher_risk_and_keeps_history(client):
    headers = await _register(client)
    await client.post("/users/me/risk-assessment", json=_STABLE_ANSWERS, headers=headers)

    second = await client.post(
        "/users/me/risk-assessment", json=_AGGRESSIVE_ANSWERS, headers=headers
    )
    assert second.status_code == 201
    assert second.json()["risk_profile"] == "공격형"
    assert second.json()["is_higher_risk_than_previous"] is True

    history = await client.get("/users/me/risk-profile/history", headers=headers)
    assert history.status_code == 200
    profiles = [entry["risk_profile"] for entry in history.json()]
    assert profiles == ["안정형", "공격형"]


async def test_suitability_requires_authentication(client):
    response = await client.get("/users/me/risk-profile")

    assert response.status_code == 401
