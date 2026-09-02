"""16번대 통합테스트 — /marketplace 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import json
import uuid
from decimal import Decimal
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


async def test_create_listing_starts_as_draft(client, pool):
    _, headers, seller_id = await _register(client)
    strategy_id, version = await _create_strategy(pool, seller_id)

    response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": "10.00"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "DRAFT"


async def test_full_listing_to_purchase_flow(client, pool, event_bus):
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, buyer_id = await _register(client)
    _, verifier_headers, verifier_id = await _register(client)
    await _make_verifier(pool, verifier_id)
    await _set_risk_profile(pool, buyer_id)
    await _fund_wallet(pool, buyer_id, Decimal("10.00"))
    strategy_id, version = await _create_strategy(pool, seller_id)

    create_response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": "10.00"},
        headers=seller_headers,
    )
    listing_id = create_response.json()["id"]

    submit_response = await client.post(
        f"/marketplace/listings/{listing_id}/submit-verification", headers=seller_headers
    )
    assert submit_response.json()["status"] == "PENDING_VERIFICATION"

    verify_response = await client.post(
        f"/marketplace/listings/{listing_id}/verify",
        json={"decision": "APPROVE"},
        headers=verifier_headers,
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["status"] == "LISTED"

    search_response = await client.get("/marketplace/listings")
    assert any(item["id"] == listing_id for item in search_response.json()["items"])

    purchase_response = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_headers, "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )
    assert purchase_response.status_code == 201
    assert purchase_response.json()["status"] == "CONFIRMED"

    topics = [topic for topic, _ in event_bus.published]
    assert "strategy.verification.completed" in topics
    assert "marketplace.purchase.requested" in topics


async def test_purchase_is_idempotent_on_retry(client, pool):
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, buyer_id = await _register(client)
    _, verifier_headers, verifier_id = await _register(client)
    await _make_verifier(pool, verifier_id)
    await _set_risk_profile(pool, buyer_id)
    await _fund_wallet(pool, buyer_id, Decimal("10.00"))
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
    await client.post(
        f"/marketplace/listings/{listing_id}/verify",
        json={"decision": "APPROVE"},
        headers=verifier_headers,
    )

    key = f"test-{uuid.uuid4().hex}"
    first = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_headers, "Idempotency-Key": key},
    )
    second = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_headers, "Idempotency-Key": key},
    )

    assert first.json()["purchase_id"] == second.json()["purchase_id"]
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy_purchases WHERE id = $1",
            first.json()["purchase_id"],
        )
    assert count == 1


async def test_self_trade_purchase_rejected(client, pool):
    _, seller_headers, seller_id = await _register(client)
    _, verifier_headers, verifier_id = await _register(client)
    await _make_verifier(pool, verifier_id)
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
    await client.post(
        f"/marketplace/listings/{listing_id}/verify",
        json={"decision": "APPROVE"},
        headers=verifier_headers,
    )

    response = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**seller_headers, "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 400


async def test_non_verifier_cannot_verify(client, pool):
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, _ = await _register(client)
    strategy_id, version = await _create_strategy(pool, seller_id)

    create_response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version},
        headers=seller_headers,
    )
    listing_id = create_response.json()["id"]
    await client.post(
        f"/marketplace/listings/{listing_id}/submit-verification", headers=seller_headers
    )

    response = await client.post(
        f"/marketplace/listings/{listing_id}/verify",
        json={"decision": "APPROVE"},
        headers=buyer_headers,
    )

    assert response.status_code == 403


async def test_strategy_definition_blocked_for_unauthorized_user(client, pool):
    _, seller_headers, seller_id = await _register(client)
    _, stranger_headers, _ = await _register(client)
    strategy_id, version = await _create_strategy(pool, seller_id)

    response = await client.get(
        f"/marketplace/strategies/{strategy_id}/{version}", headers=stranger_headers
    )

    assert response.status_code == 403


async def test_strategy_definition_accessible_to_owner(client, pool):
    _, seller_headers, seller_id = await _register(client)
    strategy_id, version = await _create_strategy(pool, seller_id)

    response = await client.get(
        f"/marketplace/strategies/{strategy_id}/{version}", headers=seller_headers
    )

    assert response.status_code == 200
    assert response.json()["strategy_id"] == strategy_id


async def test_dispute_submission_requires_own_purchase(client, pool):
    _, buyer_headers, buyer_id = await _register(client)

    response = await client.post(
        "/marketplace/disputes",
        json={"purchase_id": 999999999, "reason": "사유"},
        headers=buyer_headers,
    )

    assert response.status_code == 400


# --- 전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 회귀 테스트 ---


async def _listed_listing_via_api(client, pool, seller_headers, seller_id, price="10.00"):
    strategy_id, version = await _create_strategy(pool, seller_id)
    create_response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": price},
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


async def _wallet_balance(pool, user_id) -> Decimal:
    async with pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", uuid.UUID(user_id)
        )
    return balance if balance is not None else Decimal("0")


async def test_negative_price_listing_is_rejected_at_schema(client, pool):
    """음수 가격은 구매 시 지갑 증액 경로가 되므로 스키마에서 막는다.

    L4_platform_observability_tenancy_api_v1.0.md §708 — 422 vs 400 충돌은
    15_api_spec_rbac_v1.6.md#§15.3(400)을 택했다. 전역 RequestValidationError
    핸들러(src/api/contracts/handlers.py)가 pydantic 검증 실패를 전부 400
    VALIDATION_INVALID_FIELD로 봉투에 담아 응답한다."""
    _, headers, seller_id = await _register(client)
    strategy_id, version = await _create_strategy(pool, seller_id)

    response = await client.post(
        "/marketplace/listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": "-1000000"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_idempotency_key_is_scoped_per_user(client, pool):
    """같은 Idempotency-Key 헤더값을 다른 사용자가 보내도 서로의 구매 응답을
    받지 않고 각자 실제로 구매된다."""
    _, seller_headers, seller_id = await _register(client)
    _, buyer_a_headers, buyer_a_id = await _register(client)
    _, buyer_b_headers, buyer_b_id = await _register(client)
    for buyer_id in (buyer_a_id, buyer_b_id):
        await _set_risk_profile(pool, buyer_id)
        await _fund_wallet(pool, buyer_id, Decimal("10.00"))
    listing_id = await _listed_listing_via_api(client, pool, seller_headers, seller_id)

    key = f"shared-{uuid.uuid4().hex}"
    first = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_a_headers, "Idempotency-Key": key},
    )
    second = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_b_headers, "Idempotency-Key": key},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["purchase_id"] != second.json()["purchase_id"]
    assert await _wallet_balance(pool, buyer_a_id) == Decimal("0")
    assert await _wallet_balance(pool, buyer_b_id) == Decimal("0")


async def test_failed_purchase_is_not_cached_under_idempotency_key(client, pool):
    """잔액 부족 402는 캐시되지 않아 충전 후 같은 키로 재시도하면 구매된다.
    그 뒤 같은 키 재요청은 성공 응답을 캐시에서 돌려준다."""
    _, seller_headers, seller_id = await _register(client)
    _, buyer_headers, buyer_id = await _register(client)
    await _set_risk_profile(pool, buyer_id)
    listing_id = await _listed_listing_via_api(client, pool, seller_headers, seller_id)

    key = f"retry-{uuid.uuid4().hex}"
    headers = {**buyer_headers, "Idempotency-Key": key}
    url = f"/marketplace/listings/{listing_id}/purchase"
    denied = await client.post(url, json={}, headers=headers)
    assert denied.status_code == 402

    await _fund_wallet(pool, buyer_id, Decimal("10.00"))
    retried = await client.post(url, json={}, headers=headers)
    assert retried.status_code == 201

    replayed = await client.post(url, json={}, headers=headers)
    assert replayed.status_code == 201
    assert replayed.json()["purchase_id"] == retried.json()["purchase_id"]
    assert await _wallet_balance(pool, buyer_id) == Decimal("0")
