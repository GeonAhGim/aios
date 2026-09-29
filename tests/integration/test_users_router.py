"""16번대 통합테스트 — /users/me/approval-settings, /users/me/withdrawal-whitelist,
/users/me/delete 라우터. 실제 FastAPI 앱 + 실제 dev DB."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus
from src.api.service_deps import get_approval_settings_service
from src.core.approval.service import create_request
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.integration.conftest import NoopEventBus
from tests.integration.mfa_clock import mfa_clock_frozen, totp_at

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def event_bus():
    return NoopEventBus()


@pytest.fixture
async def client(event_bus):
    # 화이트리스트 등록이 system_safety_state(공유 싱글톤 행)의 circuit_breaker
    # 상태를 확인한다 — 다른 테스트 파일이 남긴 상태에 좌우되지 않도록 리셋.
    conn = await retry_too_many_connections(lambda: asyncpg.connect(_asyncpg_dsn()))
    await conn.execute(
        "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
        "reactivation_approval_id = NULL WHERE id = 1"
    )
    await conn.close()

    async with lifespan_context_with_retry(app):
        app.dependency_overrides[get_event_bus] = lambda: event_bus
        # raise_app_exceptions=False — 이유는 test_auth_router.py의 client
        # 픽스처 주석 참조(도메인 예외가 전역 Exception 핸들러를 거쳐도
        # httpx가 원본 예외를 재전파해 정상 처리된 4xx까지 실패로 만든다).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_event_bus, None)


@pytest.fixture
async def pool():
    p = await retry_too_many_connections(
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    )
    yield p
    await p.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[str, dict]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    return email, {"Authorization": f"Bearer {token}"}


async def _register_with_id(client) -> tuple[dict, uuid.UUID]:
    _, headers = await _register(client)
    me = await client.get("/users/me", headers=headers)
    return headers, uuid.UUID(me.json()["data"]["user_id"])


# ---------- approval settings ----------


async def test_get_approval_settings_defaults_to_solo(client):
    _, headers = await _register(client)

    response = await client.get("/users/me/approval-settings", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "SOLO"


async def test_update_approval_settings_to_dual(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings",
        json={"mode": "DUAL", "second_approver_contact": "backup@example.com"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "DUAL"


async def test_update_approval_settings_dual_without_contact_rejected(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings", json={"mode": "DUAL"}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_approval_settings_requires_authentication(client):
    response = await client.get("/users/me/approval-settings")

    assert response.status_code == 401


# ---------- withdrawal whitelist ----------


async def test_register_whitelist_entry_requires_correct_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "password": "WrongPassword1!",
        },
        headers=headers,
    )

    assert response.status_code == 403


async def test_register_and_list_whitelist_entry(client, event_bus):
    _, headers = await _register(client)

    register_response = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "label": "콜드월렛",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert register_response.status_code == 201
    assert any(topic == "security.withdrawal_whitelist.added" for topic, _ in event_bus.published)

    list_response = await client.get("/users/me/withdrawal-whitelist", headers=headers)
    assert list_response.status_code == 200
    entries = list_response.json()["data"]
    assert any(e["destination_address"] == "bc1qcoldwallet" for e in entries)


async def test_register_whitelist_entry_with_mfa_requires_totp(client):
    email, headers = await _register(client)

    # esc-ci-cbb8b9c62497 — 실시간 코드는 서버 검증과의 30초 구간 경계 레이스가
    # 있어(valid_window=0) 시계를 고정한다.
    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        verify_response = await client.post(
            "/auth/mfa/verify", json={"totp_code": totp_at(secret, frozen_now)}, headers=headers
        )
    assert verify_response.status_code == 200, verify_response.text

    without_totp = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert without_totp.status_code == 403

    # docs/RED_TEAM_FINDINGS.md #13 반영 — 위 verify()가 이미 이 구간의
    # 코드를 소비했으므로 재인증용 코드는 다음 구간에서 새로 받아야 한다.
    # 실시간 31초 대기 대신 MfaService 시계를 다음 구간의 한 시각으로 고정한다
    # (전수감사 §9; shifted는 실시간을 다시 읽어 경계 레이스가 남는다).
    # system_safety_state(전역 싱글톤 행)는 공유 DB의 다른 세션이 바꿔놨을 수
    # 있어 어서션 직전에 리셋한다.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    login_at = frozen_now + timedelta(seconds=31)
    with mfa_clock_frozen(app, login_at):
        login_code = totp_at(secret, login_at)
        with_totp = await client.post(
            "/users/me/withdrawal-whitelist",
            json={
                "exchange": "bitget",
                "destination_address": "bc1qcoldwallet",
                "password": STRONG_PASSWORD,
                "totp_code": login_code,
            },
            headers=headers,
        )
    assert with_totp.status_code == 201


# ---------- account deletion ----------


async def test_request_deletion_succeeds_with_correct_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/delete", json={"password": STRONG_PASSWORD}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "PENDING_DELETION"


async def test_request_deletion_rejects_wrong_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/delete", json={"password": "WrongPassword1!"}, headers=headers
    )

    # AccountDeletionError는 "존재하지 않는 사용자"/"비밀번호 불일치"/"이미
    # 탈퇴 대기중" 등 여러 사유를 한 클래스로 묶는다(exception_mapping.py
    # 문서화된 한계) — EXCEPTION_MAP은 대표 사유로 STATE_INVALID_TRANSITION
    # (409)에 매핑한다.
    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_relogin_after_deletion_request_cancels_it(client):
    email, headers = await _register(client)
    await client.post("/users/me/delete", json={"password": STRONG_PASSWORD}, headers=headers)

    login_response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert login_response.status_code == 200

    token = login_response.json()["data"]["access_token"]
    me_response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.json()["data"]["status"] == "ACTIVE"


# ---------- self-service 승인 요청 (FD-10.1 SOLO/DUAL 갭 해소) ----------


async def _rewind_created_at(pool, request_id: int, seconds_ago: float) -> None:
    from datetime import datetime, timedelta, timezone

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = $2 WHERE id = $1",
            request_id,
            datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        )


async def test_list_my_approval_requests_shows_own_pending_request(client, pool):
    headers, user_id = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=user_id,
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="SOLO",
    )

    response = await client.get("/users/me/approval-requests", headers=headers)

    assert response.status_code == 200
    assert any(item["id"] == request.id for item in response.json()["data"])


async def test_self_approve_solo_request_succeeds_after_wait(client, pool):
    headers, user_id = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=user_id,
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="SOLO",
    )
    await _rewind_created_at(pool, request.id, 61)

    response = await client.post(
        f"/users/me/approval-requests/{request.id}/approve", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "APPROVED"


async def test_self_approve_rejects_other_users_request(client, pool):
    _, owner_id = await _register_with_id(client)
    stranger_headers, _ = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=owner_id,
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="SOLO",
    )
    await _rewind_created_at(pool, request.id, 61)

    response = await client.post(
        f"/users/me/approval-requests/{request.id}/approve", headers=stranger_headers
    )

    assert response.status_code == 403


async def test_self_reject_own_request_succeeds(client, pool):
    headers, user_id = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=user_id,
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
    )

    response = await client.post(
        f"/users/me/approval-requests/{request.id}/reject", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "REJECTED"


async def test_self_approve_before_mandatory_wait_elapsed_returns_conflict(client, pool):
    """불변식(core/approval/service.py::approve): mandatory_wait_seconds가
    지나기 전에는 SOLO 요청도 승인할 수 없다 — created_at을 되돌리지 않고
    즉시 승인을 시도하면 409로 거부돼야 한다(강제 대기시간 우회 방지)."""
    headers, user_id = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=user_id,
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="SOLO",
    )

    response = await client.post(
        f"/users/me/approval-requests/{request.id}/approve", headers=headers
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_self_approve_nonexistent_request_returns_forbidden(client):
    """불변식(routers/users.py::_require_own_request): 자기소유 PENDING
    목록에 없는 request_id는 존재 여부를 밝히지 않고 항상 403으로 막아야
    한다 — 있지도 않은 id를 대상으로 승인을 시도해도 404/409가 아니라
    소유권 검사 단계에서 fail-closed로 거부된다."""
    headers, _ = await _register_with_id(client)

    response = await client.post("/users/me/approval-requests/999999999/approve", headers=headers)

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTHZ_FORBIDDEN"


async def test_self_reject_already_resolved_request_returns_forbidden(client, pool):
    """불변식: 요청이 REJECTED로 처리되고 나면 더 이상 "본인 소유 PENDING"
    목록에 없다 — 같은 request_id로 다시 reject를 시도하면 이중처리
    (상태 덮어쓰기)를 막기 위해 403으로 거부돼야 한다."""
    headers, user_id = await _register_with_id(client)
    request = await create_request(
        pool,
        scope="USER",
        user_id=user_id,
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
    )

    first = await client.post(f"/users/me/approval-requests/{request.id}/reject", headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/users/me/approval-requests/{request.id}/reject", headers=headers)

    assert second.status_code == 403
    assert second.json()["error_code"] == "AUTHZ_FORBIDDEN"


# ---------- negative/실패주입 (task-4091 DEEPEN) ----------


async def test_update_approval_settings_rejects_unknown_mode(client):
    """불변식(ApprovalSettingsService.update): `mode`는
    APPROVAL_MODES=('SOLO','DUAL') 화이트리스트 밖 값을 명시적으로
    거부해야 한다 — 임의 오타/미정의 모드 문자열을 그대로 저장하면 안
    된다."""
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings",
        json={"mode": "BOGUS_MODE"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_register_whitelist_entry_during_crisis_rejected(client, pool):
    """불변식(withdrawal_whitelist_service.py 모듈 docstring): 위기 상황
    (Circuit Breaker RESTRICTED 이상)에서는 새 출금 목적지 등록 자체가
    막혀야 한다 — "위기 상황이 닥친 뒤에는 등록 불가"가 실제로 강제되는지
    검증한다."""
    _, headers = await _register(client)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'restricted' WHERE id = 1"
        )
    try:
        response = await client.post(
            "/users/me/withdrawal-whitelist",
            json={
                "exchange": "bitget",
                "destination_address": "bc1qcoldwallet",
                "password": STRONG_PASSWORD,
            },
            headers=headers,
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE system_safety_state SET circuit_breaker_level = 'normal' WHERE id = 1"
            )

    assert response.status_code == 403
    assert response.json()["error_code"] == "POLICY_DENIED"


async def test_approval_settings_service_failure_propagates_as_error(client):
    """실패주입: `ApprovalSettingsService` 의존성이 예외를 던지면 그대로
    전파돼야 한다 -- fail-closed 기본값(CLAUDE.md §3)이 지켜지는지
    검증한다. 조용히 기본 SOLO 설정으로 위장하면 실제 저장된 설정 조회
    실패를 숨기게 된다."""
    _, headers = await _register(client)

    class _FailingApprovalSettingsService:
        async def get(self, user_id):
            raise RuntimeError("simulated approval-settings backend failure")

    app.dependency_overrides[get_approval_settings_service] = lambda: (
        _FailingApprovalSettingsService()
    )
    try:
        response = await client.get("/users/me/approval-settings", headers=headers)
    finally:
        app.dependency_overrides.pop(get_approval_settings_service, None)

    assert response.status_code == 500
