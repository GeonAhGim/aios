"""16번대 통합테스트 — /users/me/approval-settings, /users/me/withdrawal-whitelist,
/users/me/delete 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid
from pathlib import Path

import asyncpg
import pyotp
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus
from src.core.approval.service import create_request
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
def event_bus():
    return NoopEventBus()


@pytest.fixture
async def client(event_bus):
    # 화이트리스트 등록이 system_safety_state(공유 싱글톤 행)의 circuit_breaker
    # 상태를 확인한다 — 다른 테스트 파일이 남긴 상태에 좌우되지 않도록 리셋.
    conn = await asyncpg.connect(_asyncpg_dsn())
    await conn.execute(
        "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
        "reactivation_approval_id = NULL WHERE id = 1"
    )
    await conn.close()

    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_event_bus] = lambda: event_bus
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_event_bus, None)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[str, dict]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    return email, {"Authorization": f"Bearer {token}"}


async def _register_with_id(client) -> tuple[dict, uuid.UUID]:
    _, headers = await _register(client)
    me = await client.get("/users/me", headers=headers)
    return headers, uuid.UUID(me.json()["user_id"])


# ---------- approval settings ----------


async def test_get_approval_settings_defaults_to_solo(client):
    _, headers = await _register(client)

    response = await client.get("/users/me/approval-settings", headers=headers)

    assert response.status_code == 200
    assert response.json()["mode"] == "SOLO"


async def test_update_approval_settings_to_dual(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings",
        json={"mode": "DUAL", "second_approver_contact": "backup@example.com"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "DUAL"


async def test_update_approval_settings_dual_without_contact_rejected(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings", json={"mode": "DUAL"}, headers=headers
    )

    assert response.status_code == 400


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
    assert any(
        topic == "security.withdrawal_whitelist.added" for topic, _ in event_bus.published
    )

    list_response = await client.get("/users/me/withdrawal-whitelist", headers=headers)
    assert list_response.status_code == 200
    entries = list_response.json()
    assert any(e["destination_address"] == "bc1qcoldwallet" for e in entries)


async def test_register_whitelist_entry_with_mfa_requires_totp(client):
    email, headers = await _register(client)

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["secret"]
    code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

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
    # 실시간 31초 대기 대신 MfaService 시계를 31초 앞당긴다(전수감사 §9).
    # system_safety_state(전역 싱글톤 행)는 공유 DB의 다른 세션이 바꿔놨을 수
    # 있어 어서션 직전에 리셋한다.
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    with mfa_clock_shifted(app, 31) as shifted_now:
        login_code = totp_at(secret, shifted_now())
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
    assert response.json()["status"] == "PENDING_DELETION"


async def test_request_deletion_rejects_wrong_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/delete", json={"password": "WrongPassword1!"}, headers=headers
    )

    assert response.status_code == 400


async def test_relogin_after_deletion_request_cancels_it(client):
    email, headers = await _register(client)
    await client.post("/users/me/delete", json={"password": STRONG_PASSWORD}, headers=headers)

    login_response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    me_response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_response.json()["status"] == "ACTIVE"


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
    assert any(item["id"] == request.id for item in response.json())


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
    assert response.json()["status"] == "APPROVED"


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
    assert response.json()["status"] == "REJECTED"
