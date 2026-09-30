"""tests/integration/test_admin_router*.py 공용 fixture/helper.

RATCHET-split(task-10201): test_admin_router.py가 500줄 경고를 넘어 책임별로
분할하며, 공용 fixture·헬퍼를 여기로 모았다. 각 test_admin_router_*.py가 필요한
이름만 import한다(pytest는 fixture를 다른 모듈에서 import해 test 모듈
네임스페이스에 두는 것만으로 인식한다).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
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
from tests.integration.mfa_clock import mfa_clock_frozen, totp_at

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
        # raise_app_exceptions=False — 이유는 test_auth_router.py의 client
        # 픽스처 주석 참조(도메인 예외가 전역 Exception 핸들러를 거쳐도
        # httpx가 원본 예외를 재전파해 정상 처리된 4xx까지 실패로 만든다).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
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
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["data"]["user_id"]


async def _make_admin(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE user_id = $1", uuid.UUID(user_id)
        )


async def _mfa_verified_admin_headers(client, pool) -> dict:
    """PLT-35-fix(task-3850): `/admin/audit-log`가 이제 `require_break_glass
    ("tenant_read")`를 요구해, `get_current_admin`만으로는 더 이상 충분하지
    않다 -- 실제 MFA 설정/로그인 왕복으로 MFA_VERIFIED 토큰을 발급받는다
    (tests/integration/test_admin_break_glass_router.py의 `_register_mfa_admin`과
    동일 패턴)."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    await _make_admin(pool, me.json()["data"]["user_id"])

    # esc-ci-cbb8b9c62497 -- 실시간 코드 생성은 서버 검증과의 30초 구간 경계 레이스가
    # 있어(valid_window=0) 시계를 고정한다. 로그인 코드는 재사용 거부(RED_TEAM #13)
    # 때문에 다음 구간(+31s)으로 고정한다.
    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        verify_response = await client.post(
            "/auth/mfa/verify", json={"totp_code": totp_at(secret, frozen_now)}, headers=headers
        )
    assert verify_response.status_code == 200, verify_response.text

    login_at = frozen_now + timedelta(seconds=31)
    with mfa_clock_frozen(app, login_at):
        login_response = await client.post(
            "/auth/login",
            json={
                "email": email,
                "password": STRONG_PASSWORD,
                "totp_code": totp_at(secret, login_at),
            },
        )
    assert login_response.status_code == 200, login_response.text
    mfa_token = login_response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {mfa_token}"}


async def _approved_break_glass_grant(client, requester_headers, approver_headers, *, scope):
    request_response = await client.post(
        "/admin/break-glass/grants",
        json={"scope": scope, "reason": "test_admin_router", "ttl_minutes": 30},
        headers=requester_headers,
    )
    grant_id = request_response.json()["data"]["id"]
    await client.post(f"/admin/break-glass/grants/{grant_id}:approve", headers=approver_headers)
    return grant_id


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
        # task-1721 P1-B — submit-verification이 paper_trading_eligibility.
        # check_paper_trading_eligibility로 strategy_executions를 실제
        # 조회하므로, 이 라우터 테스트들이 검증 게이트를 통과하려면 3개월
        # 이상 된 PAPER 실행 이력을 미리 심어둬야 한다(게이트 자체를 검증
        # 하는 테스트는 tests/adversarial/marketplace/에 따로 있다).
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
