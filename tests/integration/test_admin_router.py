"""18번대 통합테스트 — /admin 라우터. 실제 FastAPI 앱 + 실제 dev DB."""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pyotp
import pytest
from dotenv import dotenv_values
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.admin_deps import get_audit_log_read_service
from src.api.deps import get_event_bus, get_pool
from src.api.service_deps import get_credential_resolver
from src.data.models.trading import AccountBalance
from src.main import app
from tests.integration.conftest import NoopEventBus
from tests.integration.mfa_clock import mfa_clock_shifted, totp_at

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

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["data"]["secret"]
    verify_code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": verify_code}, headers=headers)

    with mfa_clock_shifted(app, 31) as shifted_now:
        login_code = totp_at(secret, shifted_now())
        login_response = await client.post(
            "/auth/login",
            json={"email": email, "password": STRONG_PASSWORD, "totp_code": login_code},
        )
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
    assert any(item["listing_id"] == listing_id for item in response.json()["data"])


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
    assert any(d["id"] == dispute_id for d in list_response.json()["data"])

    detail_response = await client.get(f"/admin/disputes/{dispute_id}", headers=admin_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["dispute_id"] == dispute_id

    resolve_response = await client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"decision": "DELISTED_AND_REFUND", "reason": "환불 처리"},
        headers=admin_headers,
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["data"]["listing_status"] == "DELISTED"


async def test_dispute_endpoints_require_admin_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/disputes", headers=headers)

    assert response.status_code == 403


async def test_resolve_dispute_rejects_unknown_decision(client, pool):
    """불변식 위반: `decision`은 `VALID_DECISIONS` 화이트리스트 밖 값을
    명시적으로 거부해야 한다(임의 문자열을 그대로 저장하지 않는다)."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    dispute, _ = await _create_dispute(client, pool)
    dispute_id = dispute["dispute_id"]

    response = await client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"decision": "BOGUS_DECISION", "reason": "무효 결정"},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_resolve_dispute_twice_returns_conflict(client, pool):
    """불변식: OPEN 상태인 분쟁만 처리 가능 — 이미 RESOLVED된 분쟁을
    다시 resolve하면 409로 거부돼야 한다(중복 환불/상태 덮어쓰기 방지)."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    dispute, _ = await _create_dispute(client, pool)
    dispute_id = dispute["dispute_id"]

    first = await client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"decision": "DELISTED_AND_REFUND", "reason": "환불 처리"},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = await client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"decision": "DELISTED_AND_REFUND", "reason": "환불 처리"},
        headers=admin_headers,
    )

    assert second.status_code == 409
    assert second.json()["error_code"] == "STATE_INVALID_TRANSITION"


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
    assert response.json()["data"]["status"] == "SUSPENDED"


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
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_admin_rejects_unknown_status_value(client, pool):
    """불변식 위반: `UserStatusChangeRequest.status`는 자유 문자열이지만
    `ADMIN_SETTABLE_STATUSES`에 없는 값은 서비스 레이어가 명시적으로
    거부해야 한다 (DELETED만 막는 게 아니라 임의 오타/미정의 상태 전체)."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)

    response = await client.patch(
        f"/admin/users/{target_id}/status",
        json={"status": "BOGUS_STATUS"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_admin_change_status_for_nonexistent_user_returns_404(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)

    response = await client.patch(
        f"/admin/users/{uuid.uuid4()}/status",
        json={"status": "SUSPENDED"},
        headers=admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


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
    assert response.json()["data"]["seller_suspended"] is True


async def test_admin_can_list_audit_log(client, pool):
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    _, target_id = await _register(client)
    await client.post(
        f"/admin/users/{target_id}/suspend-seller",
        json={"reason": "판매 정책 위반"},
        headers=admin_headers,
    )

    # PLT-35-fix(task-3850): 감사로그 조회는 이제 승인된 break-glass grant를
    # 요구한다 -- 요청자 본인은 승인할 수 없으므로(I12) 별도 MFA admin이 승인한다.
    reader_headers = await _mfa_verified_admin_headers(client, pool)
    approver_headers = await _mfa_verified_admin_headers(client, pool)
    grant_id = await _approved_break_glass_grant(
        client, reader_headers, approver_headers, scope="tenant_read"
    )

    response = await client.get(
        "/admin/audit-log",
        params={"action_type": "seller.suspended", "target_id": target_id},
        headers={**reader_headers, "X-Break-Glass-Grant": grant_id},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["total"] >= 1
    assert any(item["target_id"] == target_id for item in body["items"])


async def test_audit_log_requires_admin_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/audit-log", headers=headers)

    assert response.status_code == 403


async def test_audit_log_service_failure_propagates_as_error(client, pool):
    """실패주입: `AuditLogReadService` 의존성이 예외를 던지면 그대로
    전파돼야 한다 -- fail-closed 기본값(CLAUDE.md §3)이 지켜지는지 검증한다.
    조용히 빈 목록/가짜 성공으로 위장하면 감사로그 열람 실패를 숨기게
    된다."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)

    reader_headers = await _mfa_verified_admin_headers(client, pool)
    approver_headers = await _mfa_verified_admin_headers(client, pool)
    grant_id = await _approved_break_glass_grant(
        client, reader_headers, approver_headers, scope="tenant_read"
    )

    class _FailingAuditLogService:
        async def list_entries(self, **kwargs):
            raise RuntimeError("simulated audit-log backend failure")

    app.dependency_overrides[get_audit_log_read_service] = lambda: _FailingAuditLogService()
    try:
        response = await client.get(
            "/admin/audit-log",
            params={"action_type": "seller.suspended"},
            headers={**reader_headers, "X-Break-Glass-Grant": grant_id},
        )
    finally:
        app.dependency_overrides.pop(get_audit_log_read_service, None)

    assert response.status_code == 500


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
    total = probe.json()["data"]["total"]
    pending_response = await client.get(
        "/admin/wallet/topups/pending", params={"page_size": total}, headers=admin_headers
    )
    assert pending_response.status_code == 200
    assert any(item["id"] == topup_id for item in pending_response.json()["data"]["items"])

    confirm_response = await client.post(
        f"/admin/wallet/topups/{topup_id}/confirm",
        headers={**admin_headers, "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )
    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()["data"]
    assert confirm_body["status"] == "CONFIRMED"
    assert Decimal(str(confirm_body["balance_after"])) == Decimal("30000")


async def test_confirm_topup_same_key_different_body_returns_409(client, pool):
    """P0-F(I-03, task-1719) DoD — /admin/wallet/topups/{id}/confirm도
    marketplace 구매와 동일하게 require_idempotency_key/run_idempotent로
    이관됐다 — 같은 Idempotency-Key로 다른 요청 본문이 재전송되면 409
    INTEGRITY_IDEMPOTENCY_CONFLICT다."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    user_headers, _ = await _register(client)

    topup_response = await client.post(
        "/wallet/topup-requests", json={"amount": "30000"}, headers=user_headers
    )
    topup_id = topup_response.json()["id"]

    key = f"conflict-{uuid.uuid4().hex}"
    headers = {**admin_headers, "Idempotency-Key": key}
    url = f"/admin/wallet/topups/{topup_id}/confirm"

    first = await client.post(url, json={}, headers=headers)
    second = await client.post(url, json={"note": "different-body"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error_code"] == "INTEGRITY_IDEMPOTENCY_CONFLICT"


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
    body = response.json()["data"]
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


async def test_admin_can_list_pending_approval_requests(client, pool):
    from src.core.approval.service import create_request

    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)
    platform_request = await create_request(
        pool,
        scope="PLATFORM",
        trigger_source="circuit_breaker_reactivation",
        requested_action="REACTIVATE",
        context={},
        approval_mode="SOLO",
    )

    response = await client.get(
        "/admin/approval-requests/pending", params={"scope": "PLATFORM"}, headers=admin_headers
    )

    assert response.status_code == 200
    assert any(item["id"] == platform_request.id for item in response.json()["data"])


async def test_pending_approval_requests_require_admin_role(client):
    headers, _ = await _register(client)

    response = await client.get("/admin/approval-requests/pending", headers=headers)

    assert response.status_code == 403


async def test_approve_nonexistent_approval_request_returns_conflict(client, pool):
    """불변식 위반: 존재하지 않는 approval request id도 `ApprovalError`로
    잡혀 409(STATE_INVALID_TRANSITION)로 응답해야 한다 — 500으로 새거나
    조용히 성공 취급하면 안 된다."""
    admin_headers, admin_id = await _register(client)
    await _make_admin(pool, admin_id)

    response = await client.post(
        "/admin/approval-requests/999999999/approve", headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_reject_already_rejected_approval_request_returns_conflict(client, pool):
    """불변식: PENDING 요청만 reject 가능 — 이미 REJECTED된 요청을 다시
    reject하면 409로 거부돼야 한다(이중 처리 방지)."""
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

    first = await client.post(
        f"/admin/approval-requests/{request_id}/reject", headers=admin_headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/admin/approval-requests/{request_id}/reject", headers=admin_headers
    )

    assert second.status_code == 409
    assert second.json()["error_code"] == "STATE_INVALID_TRANSITION"


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
            "UPDATE approval_requests SET created_at = now() - interval '2 minutes' WHERE id = $1",
            request_id,
        )

    approve_response = await client.post(
        f"/admin/approval-requests/{request_id}/approve", headers=admin_headers
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["data"]["status"] == "APPROVED"


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
    assert response.json()["data"]["status"] == "REJECTED"


async def test_admin_endpoints_require_authentication(client):
    response = await client.get("/admin/users")

    assert response.status_code == 401
