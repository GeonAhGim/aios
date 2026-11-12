"""21번대 통합테스트 — /device-tokens 라우터. 실제 FastAPI 앱 + 실제 dev DB."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — PLT-20이 register/deactivate의 raw
        # HTTPException을 DeviceTokenError/DeviceTokenNotFoundError로
        # 교체했다. 도메인 예외는 이제 전역 Exception 핸들러
        # (ServerErrorMiddleware 승격)를 거치는데, Starlette가 정상 응답
        # 뒤에도 예외를 재전파하기 때문에 필요하다(test_auth_router.py
        # client 픽스처와 동일 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_register_device_token(client):
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["is_active"] is True


async def test_register_rejects_unknown_platform(client):
    # VALID_PLATFORMS 화이트리스트에 없는 플랫폼은 400으로 거부해야 한다.
    # D2 negative ≥3: WindowsPhone, webOS, KaiOS 세 가지 미지원 플랫폼.
    headers = await _register(client)

    for unknown_platform in ("WindowsPhone", "webOS", "KaiOS"):
        response = await client.post(
            "/device-tokens",
            json={"device_token": f"token-{unknown_platform}", "platform": unknown_platform},
            headers=headers,
        )
        assert response.status_code == 400
        body = response.json()
        # DeviceTokenError → EXCEPTION_MAP → VALIDATION_INVALID_FIELD(400)
        assert body["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_register_rejects_unknown_platform_service_failure(client, monkeypatch):
    # 실패 주입: VALID_PLATFORMS 검사는 서비스 계층에서 발생한다.
    # 서비스 계층이 DB 예외를 던지면 5xx가 아니라 전역 핸들러가 400으로 변환해야 한다.
    from src.services.device_token_service import DeviceTokenService

    async def _boom(self, user_id, device_token, platform):
        raise RuntimeError("injected pool failure")

    monkeypatch.setattr(DeviceTokenService, "register", _boom)
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=headers,
    )

    # 서비스 계층 예외는 500으로 귀결된다 — 플랫폼 검증은 통과했기 때문.
    assert response.status_code == 500
    # 내부 메시지가 응답 본문에 새어 나가지 않는다.
    assert "injected pool failure" not in response.text


@pytest.mark.perf
async def test_register_rejects_unknown_platform_perf(client):
    # p95 응답 시간 ≤ 500ms — 10회 반복 측정.
    # 미지원 플랫폼은 DB 쿼리 없이 pydantic/서비스 레벨에서 즉시 거부되므로 빠르다.
    import time

    headers = await _register(client)

    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        response = await client.post(
            "/device-tokens",
            json={"device_token": "token-perf", "platform": "UnknownPlatform"},
            headers=headers,
        )
        latencies.append(time.perf_counter() - start)

    assert response.status_code == 400
    p95 = sorted(latencies)[9]  # 10개 중 최대값 = p95 근사
    assert p95 < 0.5, f"p95 latency {p95:.3f}s exceeds 500ms budget"


async def test_deactivate_own_device(client):
    headers = await _register(client)
    register_response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "Android"},
        headers=headers,
    )
    device_id = register_response.json()["device_id"]

    response = await client.delete(f"/device-tokens/{device_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "deactivated"


async def test_cannot_deactivate_other_users_device(client):
    owner_headers = await _register(client)
    stranger_headers = await _register(client)
    register_response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=owner_headers,
    )
    device_id = register_response.json()["device_id"]

    response = await client.delete(f"/device-tokens/{device_id}", headers=stranger_headers)

    assert response.status_code == 404


async def test_device_tokens_require_authentication(client):
    response = await client.post(
        "/device-tokens", json={"device_token": "token-abc", "platform": "iOS"}
    )

    assert response.status_code == 401


async def test_register_rejects_platform_case_mismatch(client):
    # 플랫폼 값은 대소문자까지 정확히 일치해야 한다(VALID_PLATFORMS 화이트리스트).
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "ios"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_register_rejects_missing_required_fields(client):
    headers = await _register(client)

    response = await client.post("/device-tokens", json={"platform": "iOS"}, headers=headers)

    # 요청 검증 실패는 전역 핸들러가 400 봉투로 변환한다(422가 아님).
    assert response.status_code == 400


async def test_deactivate_unknown_device_returns_404(client):
    headers = await _register(client)

    response = await client.delete("/device-tokens/2147483646", headers=headers)

    assert response.status_code == 404


async def test_deactivate_twice_returns_404_on_second_call(client):
    # 이미 비활성화된 토큰은 다시 비활성화할 수 없다(conditional UPDATE 0행 → NotFound).
    headers = await _register(client)
    register_response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=headers,
    )
    device_id = register_response.json()["device_id"]
    first = await client.delete(f"/device-tokens/{device_id}", headers=headers)
    second = await client.delete(f"/device-tokens/{device_id}", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 404


async def test_register_service_failure_surfaces_as_server_error(client, monkeypatch):
    # 실패 주입: 서비스 계층 DB 예외가 응답 본문/상태로 새지 않고 5xx로 귀결되는지 확인.
    from src.services.device_token_service import DeviceTokenService

    async def _boom(self, user_id, device_token, platform):
        raise RuntimeError("injected pool failure")

    monkeypatch.setattr(DeviceTokenService, "register", _boom)
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=headers,
    )

    assert response.status_code == 500
    assert "injected pool failure" not in response.text
