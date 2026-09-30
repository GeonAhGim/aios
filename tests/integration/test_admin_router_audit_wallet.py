"""18번대 통합테스트 — /admin 감사로그·지갑 충전 승인. 실제 FastAPI 앱 + 실제 dev DB.

RATCHET-split(task-10201): test_admin_router.py 500줄 경고 분할 — 감사로그
열람/지갑 충전 확인 책임만 담는다. 공용 fixture/helper는
tests/integration/_admin_router_support.py.
"""

import uuid
from decimal import Decimal

from src.api.admin_deps import get_audit_log_read_service
from src.main import app
from tests.integration._admin_router_support import (
    _approved_break_glass_grant as _approved_break_glass_grant,
)
from tests.integration._admin_router_support import _make_admin as _make_admin
from tests.integration._admin_router_support import (
    _mfa_verified_admin_headers as _mfa_verified_admin_headers,
)
from tests.integration._admin_router_support import _register as _register
from tests.integration._admin_router_support import (
    client,
    event_bus,
    pool,
)

__all__ = ["client", "event_bus", "pool"]


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
