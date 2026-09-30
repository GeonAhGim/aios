"""17번대 통합테스트 — /notifications 라우터. 실제 FastAPI 앱 + 실제 dev DB."""

import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app

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
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — 전역 Exception 핸들러가 500으로 승격한
        # 뒤에도 Starlette가 원본 예외를 재전파하므로(test_alerts_router.py
        # client 픽스처와 동일 근거) 실패주입 테스트가 진짜 HTTP 응답을 보려면
        # 필요하다.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


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


async def _insert_notification(pool, user_id, event_type="marketplace.purchase.requested"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO notifications (user_id, event_type, channel, status) "
            "VALUES ($1, $2, 'EMAIL', 'SENT')",
            uuid.UUID(user_id),
            event_type,
        )


async def test_history_empty_by_default(client):
    headers, _ = await _register(client)

    response = await client.get("/notifications/history", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_history_returns_inserted_notification(client, pool):
    headers, user_id = await _register(client)
    await _insert_notification(pool, user_id)

    response = await client.get("/notifications/history", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["event_type"] == "marketplace.purchase.requested"


async def test_history_filters_by_event_type(client, pool):
    headers, user_id = await _register(client)
    await _insert_notification(pool, user_id, event_type="risk_profile.match.warned")
    await _insert_notification(pool, user_id, event_type="marketplace.purchase.requested")

    response = await client.get(
        "/notifications/history",
        params={"event_type": "risk_profile.match.warned"},
        headers=headers,
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["event_type"] == "risk_profile.match.warned"


async def test_get_preferences_defaults_to_all_true(client):
    headers, _ = await _register(client)

    response = await client.get("/notifications/preferences", headers=headers)

    assert response.status_code == 200
    assert all(v is True for v in response.json().values())


async def test_update_preferences_applies_allowed_and_rejects_unknown(client):
    headers, _ = await _register(client)

    response = await client.put(
        "/notifications/preferences",
        json={"marketplace_purchase_email": False, "unknown_field": True},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"]["marketplace_purchase_email"] is False
    assert body["rejected_fields"] == ["unknown_field"]


async def test_notifications_require_authentication(client):
    response = await client.get("/notifications/history")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Negative tests — 불변식 위반 입력을 명시적으로 거부
# ---------------------------------------------------------------------------


async def test_history_rejects_sql_injection_in_event_type(client, pool):
    """I-01 준수: event_type 필터에 SQL 인젝션 시도가 들어오면
    바인드 파라미터로 처리되어 매칭되지 않아야 한다(에러가 아닌 빈 결과)."""
    headers, user_id = await _register(client)
    await _insert_notification(pool, user_id)
    # 실제 이벤트 타입과 무관한 인젝션 시드 — 바인드 파라미터라면 0건 반환
    response = await client.get(
        "/notifications/history",
        params={"event_type": "'; DROP TABLE notifications; --"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_update_preferences_rejects_all_unknown_fields(client):
    """FD-17.4 준수: 허용 목록 밖 필드만 보내면 전부 rejected_fields로 보고되고
    DB에는 아무 변경도 적용되지 않는다(기본값 유지)."""
    headers, _ = await _register(client)

    response = await client.put(
        "/notifications/preferences",
        json={"unknown_field_a": True, "unknown_field_b": False},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }
    assert sorted(body["rejected_fields"]) == ["unknown_field_a", "unknown_field_b"]


async def test_update_preferences_rejects_non_bool_value(client):
    """스키마 불변식: dict[str, bool] 스키마가 bool로 강제 변환할 수 없는 값
    (객체/리스트)이 들어오면 400으로 거부한다(이 앱의 validation-error 매핑은
    422가 아닌 400 — src/api/contracts/exception_mapping.py)."""
    headers, _ = await _register(client)

    response = await client.put(
        "/notifications/preferences",
        json={"marketplace_purchase_email": {"nested": "object"}},
        headers=headers,
    )

    assert response.status_code == 400


async def test_update_preferences_empty_changes_returns_all_defaults(client):
    """FD-17.4 준수: 빈 변경 요청은 에러가 아닌 전체 기본값을 반환한다."""
    headers, _ = await _register(client)

    response = await client.put(
        "/notifications/preferences",
        json={},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }
    assert body["rejected_fields"] == []


# ---------------------------------------------------------------------------
# 실패주입 테스트 — 의존성 예외 유발
# ---------------------------------------------------------------------------


async def test_history_handles_db_query_failure(client, monkeypatch):
    """DB 쿼리 실패 시 500을 반환한다 — silent fallback 없이 fail-closed.

    test_alerts_router.py::test_create_alert_dependency_failure_returns_500_not_silent_success와
    동일한 패턴 — 라우터가 호출하는 도메인 함수 자체를 monkeypatch로 예외 발생시켜
    실제 asyncpg 커넥션/풀 내부 구조에 손대지 않는다.
    """
    import src.api.routers.notifications as notifications_router_module

    async def _boom(*args, **kwargs):
        raise asyncpg.PostgresError("simulated DB failure")

    monkeypatch.setattr(notifications_router_module, "list_notification_history", _boom)
    headers, _ = await _register(client)

    response = await client.get("/notifications/history", headers=headers)

    # FastAPI는 처리되지 않은 예외를 500으로 반환
    assert response.status_code == 500
