"""18번대 통합테스트 — /admin 플랫폼 리스팅·실행 승인 요청. 실제 FastAPI 앱 + 실제 dev DB.

RATCHET-split(task-10201): test_admin_router.py 500줄 경고 분할 — 플랫폼
리스팅 생성/실행 승인·거절 책임만 담는다. 공용 fixture/helper는
tests/integration/_admin_router_support.py.
"""

from tests.integration._admin_router_support import (
    _create_approved_strategy as _create_approved_strategy,
)
from tests.integration._admin_router_support import _create_strategy as _create_strategy
from tests.integration._admin_router_support import _link_credential as _link_credential
from tests.integration._admin_router_support import _make_admin as _make_admin
from tests.integration._admin_router_support import _register as _register
from tests.integration._admin_router_support import (
    client,
    event_bus,
    pool,
)

__all__ = ["client", "event_bus", "pool"]


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
