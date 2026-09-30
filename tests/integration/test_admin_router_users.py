"""18번대 통합테스트 — /admin 사용자 상태 관리. 실제 FastAPI 앱 + 실제 dev DB.

RATCHET-split(task-10201): test_admin_router.py 500줄 경고 분할 — 사용자 상태
변경/셀러 정지 책임만 담는다. 공용 fixture/helper는
tests/integration/_admin_router_support.py.
"""

import uuid

from tests.integration._admin_router_support import _make_admin as _make_admin
from tests.integration._admin_router_support import _register as _register
from tests.integration._admin_router_support import (
    client,
    event_bus,
    pool,
)

__all__ = ["client", "event_bus", "pool"]


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


async def test_admin_endpoints_require_authentication(client):
    response = await client.get("/admin/users")

    assert response.status_code == 401
