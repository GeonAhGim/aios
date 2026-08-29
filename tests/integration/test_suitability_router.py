"""15번대 통합테스트 — /users/me/risk-* 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import json
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus
from src.main import app
from tests.integration.conftest import NoopEventBus

STRONG_PASSWORD = "Str0ng!Passw0rd"


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
def event_bus():
    return NoopEventBus()


@pytest.fixture
async def client(event_bus):
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_event_bus] = lambda: event_bus
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_event_bus, None)


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


_NEUTRAL_ANSWERS = {
    "years_of_experience": 5,
    "investable_ratio_pct": 40,
    "loss_tolerance_pct": 20,
    "investment_goal": "SHORT_TERM_PROFIT",
    "liquidity_need": "1_TO_3_YEARS",
}


async def _create_running_execution(pool, user_id: str, risk_level: str) -> None:
    strategy_id = f"risk-warn-strategy-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status, risk_level)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED', $4)
            """,
            strategy_id,
            uuid.UUID(user_id),
            json.dumps({}),
            risk_level,
        )
        await conn.execute(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            """,
            strategy_id,
            uuid.UUID(user_id),
        )


async def test_reassessment_warns_on_running_execution_still_mismatched(
    client, pool, event_bus
):
    headers = await _register(client)
    me = await client.get("/users/me", headers=headers)
    user_id = me.json()["user_id"]

    await client.post("/users/me/risk-assessment", json=_STABLE_ANSWERS, headers=headers)
    await _create_running_execution(pool, user_id, risk_level="공격형")

    # 안정형 -> 중립형: 등급은 올라갔지만(고위험 쪽) 공격형 실행은 여전히 불일치.
    response = await client.post(
        "/users/me/risk-assessment", json=_NEUTRAL_ANSWERS, headers=headers
    )

    assert response.status_code == 201
    assert response.json()["risk_profile"] == "중립형"
    assert response.json()["is_higher_risk_than_previous"] is True
    assert any(
        topic == "risk_profile.match.warned" for topic, _ in event_bus.published
    )


async def test_reassessment_to_still_mismatched_neutral_does_not_warn_for_matching_execution(
    client, pool, event_bus
):
    headers = await _register(client)
    me = await client.get("/users/me", headers=headers)
    user_id = me.json()["user_id"]

    await client.post("/users/me/risk-assessment", json=_STABLE_ANSWERS, headers=headers)
    await _create_running_execution(pool, user_id, risk_level="안정형")

    response = await client.post(
        "/users/me/risk-assessment", json=_NEUTRAL_ANSWERS, headers=headers
    )

    assert response.status_code == 201
    assert not any(
        topic == "risk_profile.match.warned" for topic, _ in event_bus.published
    )


async def test_suitability_requires_authentication(client):
    response = await client.get("/users/me/risk-profile")

    assert response.status_code == 401
