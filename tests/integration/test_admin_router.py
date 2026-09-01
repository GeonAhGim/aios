"""18번대 통합테스트 — /admin 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus, get_pool
from src.api.service_deps import get_credential_resolver
from src.data.models.trading import AccountBalance
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


class _FakeAdapter:
    async def get_balance(self):
        return [
            AccountBalance(
                exchange="bitget",
                asset="USDT",
                total=Decimal("10000"),
                available=Decimal("10000"),
            )
        ]


class _FakeResolver:
    async def get_adapter(self, user_id, exchange):
        return _FakeAdapter()


async def _override_resolver(pool=Depends(get_pool)):
    return _FakeResolver()


@pytest.fixture
def event_bus():
    return NoopEventBus()


@pytest.fixture
async def client(event_bus):
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        app.dependency_overrides[get_event_bus] = lambda: event_bus
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_credential_resolver, None)
        app.dependency_overrides.pop(get_event_bus, None)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[dict, str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["user_id"]


async def _make_admin(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE user_id = $1", uuid.UUID(user_id)
        )


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


async def _create_approved_strategy(pool, owner_user_id, *, certified_badge=True):
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status, certified_badge)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED', $5)
            """,
            strategy_id,
            version,
            uuid.UUID(owner_user_id),
            json.dumps({}),
            certified_badge,
        )
    return strategy_id, version


async def _link_credential(pool, user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            uuid.UUID(user_id),
            exchange,
            b"dummy",
        )


async def test_verification_queue_visible_to_verifier(client, pool):
    seller_headers, seller_id = await _register(client)
    verifier_headers, verifier_id = await _register(client)
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

    response = await client.get("/admin/verification-queue", headers=verifier_headers)

    assert response.status_code == 200
    assert any(item["listing_id"] == listing_id for item in response.json())


async def test_verification_queue_requires_verifier_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/verification-queue", headers=headers)

    assert response.status_code == 403


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            uuid.UUID(user_id),
            amount,
        )


async def _create_dispute(client, pool) -> tuple[dict, int]:
    seller_headers, seller_id = await _register(client)
    buyer_headers, buyer_id = await _register(client)
    verifier_headers, verifier_id = await _register(client)
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
    purchase_response = await client.post(
        f"/marketplace/listings/{listing_id}/purchase",
        json={},
        headers={**buyer_headers, "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )
    purchase_id = purchase_response.json()["purchase_id"]

    dispute_response = await client.post(
        "/marketplace/disputes",
        json={"purchase_id": purchase_id, "reason": "설명과 다름"},
        headers=buyer_headers,
    )
    return dispute_response.json(), purchase_id


async def test_admin_can_list_and_resolve_dispute(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    dispute, _ = await _create_dispute(client, pool)
    dispute_id = dispute["dispute_id"]

    list_response = await client.get("/admin/disputes", headers=admin_headers)
    assert list_response.status_code == 200
    assert any(d["id"] == dispute_id for d in list_response.json())

    detail_response = await client.get(f"/admin/disputes/{dispute_id}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["dispute_id"] == dispute_id

    resolve_response = await client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"decision": "DELISTED_AND_REFUND", "reason": "환불 처리"},
        headers=admin_headers,
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["listing_status"] == "DELISTED"


async def test_dispute_endpoints_require_admin_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/disputes", headers=headers)

    assert response.status_code == 403


async def test_admin_can_list_and_change_user_status(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    list_response = await client.get(
        "/admin/users", params={"email_search": ""}, headers=admin_headers
    )
    assert list_response.status_code == 200

    response = await client.patch(
        f"/admin/users/{target_id}/status", json={"status": "SUSPENDED"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUSPENDED"


async def test_suspended_user_existing_token_is_rejected(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    target_headers, target_id = await _register(client)

    before = await client.get("/users/me", headers=target_headers)
    assert before.status_code == 200

    status_response = await client.patch(
        f"/admin/users/{target_id}/status", json={"status": "SUSPENDED"}, headers=admin_headers
    )
    assert status_response.status_code == 200

    after = await client.get("/users/me", headers=target_headers)
    assert after.status_code == 401


async def test_admin_cannot_set_deleted_status(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    response = await client.patch(
        f"/admin/users/{target_id}/status", json={"status": "DELETED"}, headers=admin_headers
    )

    assert response.status_code == 400


async def test_admin_can_suspend_seller(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    response = await client.post(
        f"/admin/users/{target_id}/suspend-seller",
        json={"reason": "판매 정책 위반"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["seller_suspended"] is True


async def test_admin_can_list_audit_log(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)
    await client.post(
        f"/admin/users/{target_id}/suspend-seller",
        json={"reason": "판매 정책 위반"},
        headers=admin_headers,
    )

    response = await client.get(
        "/admin/audit-log",
        params={"action_type": "seller.suspended", "target_id": target_id},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(item["target_id"] == target_id for item in body["items"])


async def test_audit_log_requires_admin_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/audit-log", headers=headers)

    assert response.status_code == 403


async def test_admin_can_view_and_confirm_pending_wallet_topup(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    user_headers, _ = await _register(client)

    topup_response = await client.post(
        "/wallet/topup-requests", json={"amount": "30000"}, headers=user_headers
    )
    assert topup_response.status_code == 200
    topup_id = topup_response.json()["id"]

    probe = await client.get(
        "/admin/wallet/topups/pending", params={"page_size": 1}, headers=admin_headers
    )
    total = probe.json()["total"]
    pending_response = await client.get(
        "/admin/wallet/topups/pending", params={"page_size": total}, headers=admin_headers
    )
    assert pending_response.status_code == 200
    assert any(item["id"] == topup_id for item in pending_response.json()["items"])

    confirm_response = await client.post(
        f"/admin/wallet/topups/{topup_id}/confirm",
        headers={**admin_headers, "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "CONFIRMED"
    assert Decimal(str(confirm_response.json()["balance_after"])) == Decimal("30000")


async def test_admin_can_create_platform_listing(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    strategy_id, version = await _create_strategy(pool, admin_id)

    response = await client.post(
        "/admin/marketplace/platform-listings",
        json={"strategy_id": strategy_id, "strategy_version": version, "price": "20.00"},
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["seller_type"] == "PLATFORM"
    assert body["status"] == "LISTED"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT seller_type, status FROM strategy_listings WHERE id = $1", body["id"]
        )
    assert row["seller_type"] == "PLATFORM"
    assert row["status"] == "LISTED"


async def test_platform_listing_endpoint_requires_admin(client):
    headers, _ = await _register(client)

    response = await client.post(
        "/admin/marketplace/platform-listings",
        json={"strategy_id": "nonexistent", "strategy_version": "1.0.0"},
        headers=headers,
    )

    assert response.status_code == 403


async def test_admin_can_approve_live_execution_request(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    owner_headers, owner_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, owner_id)
    await _link_credential(pool, owner_id)

    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "LIVE",
        },
        headers=owner_headers,
    )
    request_id = create_response.json()["approval_request_id"]

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = now() - interval '2 minutes' "
            "WHERE id = $1",
            request_id,
        )

    approve_response = await client.post(
        f"/admin/approval-requests/{request_id}/approve", headers=admin_headers
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "APPROVED"


async def test_admin_can_reject_approval_request(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    owner_headers, owner_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, owner_id)
    await _link_credential(pool, owner_id)

    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "LIVE",
        },
        headers=owner_headers,
    )
    request_id = create_response.json()["approval_request_id"]

    response = await client.post(
        f"/admin/approval-requests/{request_id}/reject", headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


async def test_admin_endpoints_require_authentication(client):
    response = await client.get("/admin/users")

    assert response.status_code == 401
