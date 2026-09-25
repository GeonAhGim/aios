"""통합테스트 — task-4093 DEEPEN(원 리프 task-116: CI 적색 해소 ruff F841
test_event_bus_laten 대상 DEEPEN): /marketplace 라우터 negative/실패주입 보강.

`test_marketplace_router.py`는 negative test 0건(<3), 실패주입/성능단언
마커가 없었다(task-4084 DEEPEN 기준). 원 파일에 직접 추가하면 477줄에서
634줄로 500줄 loc 래칫 임계를 넘어 check_code_ratchets.py의 loc_over_500
베이스라인이 올라가므로(task-4144 DEEPEN과 동일 사유), 별도 파일로
분리하고 fixture/helper를 자체 보유한다.

Spec: docs/specs/L4_marketplace_v1.0.md.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus
from src.api.marketplace_deps import get_purchase_service
from src.main import app
from src.services.purchase_service import PurchaseService
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
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_event_bus, None)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[str, dict, str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    body = response.json()
    token = body["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return email, headers, me.json()["data"]["user_id"]


async def _create_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')
            """,
            strategy_id,
            version,
            uuid.UUID(owner_user_id),
            json.dumps({}),
        )
        # task-1721 P1-B — submit-verification이 paper_trading_eligibility.
        # check_paper_trading_eligibility로 strategy_executions를 실제
        # 조회하므로, 검증 게이트를 통과하려면 3개월 이상 된 PAPER 실행
        # 이력을 미리 심어둬야 한다.
        await conn.execute(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, started_at)
            VALUES ($1, $2, $3, 'bitget', 'PAPER', 1000, now() - interval '4 months')
            """,
            strategy_id,
            version,
            uuid.UUID(owner_user_id),
        )
    return strategy_id, version


async def _make_verifier(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_verifier = true WHERE user_id = $1", uuid.UUID(user_id)
        )


async def _set_risk_profile(pool, user_id, profile="공격형"):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET risk_profile = $2, risk_profile_assessed_at = now() "
            "WHERE user_id = $1",
            uuid.UUID(user_id),
            profile,
        )


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            uuid.UUID(user_id),
            amount,
        )


async def _verified_listing(client, pool, seller_headers, seller_id) -> str:
    strategy_id, version = await _create_strategy(pool, seller_id)
    create_response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": "10.00"},
        headers=seller_headers,
    )
    listing_id = create_response.json()["id"]
    await client.post(
        f"/marketplace/listings/{listing_id}/submit-verification", headers=seller_headers
    )
    _, verifier_headers, verifier_id = await _register(client)
    await _make_verifier(pool, verifier_id)
    await client.post(
        f"/marketplace/listings/{listing_id}/verify",
        json={"decision": "APPROVE"},
        headers=verifier_headers,
    )
    return listing_id


async def test_purchase_to_nonexistent_listing_returns_400(client, pool):
    """task-4093 — 존재하지 않는 리스팅 ID로 구매하면 400 VALIDATION_INVALID_FIELD.

    purchase_service.purchase()가 listing 조회 실패 시 PurchaseError를
    던지고, exception_mapping.py가 PurchaseError를 VALIDATION_INVALID_FIELD(400)로
    매핑한다."""
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, buyer_id = await _register(client)
    await _set_risk_profile(pool, buyer_id)
    await _fund_wallet(pool, buyer_id, Decimal("10.00"))

    response = await client.post(
        "/marketplace/listings/999999999/purchase",
        json={},
        headers={**buyer_headers, "Idempotency-Key": f"nope-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_create_review_without_purchase_history_returns_400(client, pool):
    """task-4093 — 구매 이력 없는 사용자가 리뷰를 작성하면 400 BAD_REQUEST.

    review_service.create_review()가 구매 조회 실패 시 ReviewError를
    던지고, 라우터가 400 VALIDATION_INVALID_FIELD로 변환한다."""
    _, seller_headers, seller_id = await _register(client)
    _, reviewer_headers, reviewer_id = await _register(client)
    listing_id = await _verified_listing(client, pool, seller_headers, seller_id)

    response = await client.post(
        f"/marketplace/listings/{listing_id}/reviews",
        json={"rating": 5, "comment": "좋음"},
        headers=reviewer_headers,
    )

    assert response.status_code == 400


async def test_create_review_invalid_rating_returns_400(client, pool):
    """task-4093 — rating이 1~5 범위를 벗어나면 400 BAD_REQUEST.

    review_service.create_review()가 1~5 범위를 검증하고 ReviewError를 던진다."""
    _, seller_headers, seller_id = await _register(client)
    _, reviewer_headers, reviewer_id = await _register(client)
    listing_id = await _verified_listing(client, pool, seller_headers, seller_id)

    # rating=0 (범위 미만)
    response = await client.post(
        f"/marketplace/listings/{listing_id}/reviews",
        json={"rating": 0, "comment": "too low"},
        headers=reviewer_headers,
    )

    assert response.status_code == 400

    # rating=6 (범위 초과)
    response = await client.post(
        f"/marketplace/listings/{listing_id}/reviews",
        json={"rating": 6, "comment": "too high"},
        headers=reviewer_headers,
    )

    assert response.status_code == 400


async def test_purchase_service_failure_returns_500(client, pool):
    """task-4093 실패주입 — PurchaseService.purchase()가 asyncpg.PostgresError를
    던지면 전역 핸들러가 INTERNAL_ERROR(500)로 변환한다.

    monkeypatch로 의존성(service)을 AsyncMock 대역으로 교체하고
    실제 DB 오류를 시뮬레이션한다."""
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, buyer_id = await _register(client)
    await _set_risk_profile(pool, buyer_id)
    await _fund_wallet(pool, buyer_id, Decimal("10.00"))
    listing_id = await _verified_listing(client, pool, seller_headers, seller_id)

    mock_service = AsyncMock(spec=PurchaseService)
    # asyncpg.PostgresError → INTERNAL_ERROR(500) 매핑(exception_mapping.py)
    mock_service.purchase = AsyncMock(side_effect=asyncpg.PostgresError("connection refused"))

    app.dependency_overrides[get_purchase_service] = lambda: mock_service

    try:
        response = await client.post(
            f"/marketplace/listings/{listing_id}/purchase",
            json={},
            headers={**buyer_headers, "Idempotency-Key": f"fail-{uuid.uuid4().hex}"},
        )
        # asyncpg.PostgresError는 전역 핸들러가 INTERNAL_ERROR(500)로 변환한다.
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_purchase_service, None)
