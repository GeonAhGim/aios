"""18번대 통합테스트 — /admin 검증 큐·분쟁 처리. 실제 FastAPI 앱 + 실제 dev DB.

RATCHET-split(task-10201): test_admin_router.py 500줄 경고 분할 — 검증 큐/분쟁
책임만 담는다. 공용 fixture/helper는 tests/integration/_admin_router_support.py.
"""

from tests.integration._admin_router_support import _create_dispute as _create_dispute
from tests.integration._admin_router_support import _create_strategy as _create_strategy
from tests.integration._admin_router_support import _make_admin as _make_admin
from tests.integration._admin_router_support import _make_verifier as _make_verifier
from tests.integration._admin_router_support import _register as _register
from tests.integration._admin_router_support import (
    client,
    event_bus,
    pool,
)

__all__ = ["client", "event_bus", "pool"]


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
