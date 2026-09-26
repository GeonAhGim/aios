"""PLT-17/PLT-18/PLT-19/PLT-20 — auth/users 응답이 실제로 `ApiResponse` 봉투(`{"data": ...,
"meta": {"trace_id", "as_of"}}`)인지, 이관된 라우터 전부의 에러 응답이 §15.3
ApiError 포맷인지 실제 FastAPI 앱 + 실제 dev DB로 왕복 확인한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21,
src/api/contracts/envelope.py

mock으로 ApiResponse를 직접 만들어 검증하는 동어반복을 피하기 위해 실제
HTTP 요청(httpx ASGITransport)으로 앱을 왕복한다.

exchange_credentials/marketplace/strategy_builder/suitability/notifications/
alerts/device_tokens/wallet/executions/portfolio/reports의 성공 응답
봉투화는 보류했다(task-1002 PLT-17 needs_decision → task-1009 PLT-18,
task-1017 PLT-20 PM 선반영, task-1016 PLT-19도 동일 선반영으로 확정, 각
라우터 모듈 docstring 참조) — spec §2.3(line 307)이 이 변경을 MAJOR로
규정하고 `/api/v1` 경로에만 적용하라고 명시하는데, 그 경로를 여는 `mount_v1`
배선(PLT-16, src/api/versioning.py)이 아직 `src/main.py`에 없어 legacy 단일
경로만 존재하는 지금 감싸면 `contracts/openapi/v1.json` 베이스라인 대비
MAJOR 위반이 난다. 그래서 이 파일은 이 라우터들에 대해 "에러 응답은 이미
ApiError 포맷"이라는, 이번 변경 전후로 항상 참인 사실만 검증한다 — 성공
응답이 봉투라고 거짓 주장하지 않는다.
"""

from __future__ import annotations

import re
import uuid
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — tests/integration/test_auth_router.py
        # client 픽스처와 동일 근거(도메인 예외는 전역 Exception 핸들러를
        # 거치고, Starlette가 정상 응답 뒤에도 예외를 재전파하기 때문).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register_user(client: AsyncClient) -> tuple[dict, dict]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    body = response.json()
    headers = {"Authorization": f"Bearer {body['data']['access_token']}"}
    return body, headers


def _assert_success_envelope(body: dict) -> None:
    """`ApiResponse`가 실제로 만드는 봉투 모양 — `data`와
    `meta.trace_id`(UUID)·`meta.as_of`가 최상위에 있어야 한다. 최상위가
    바로 배열/딕셔너리 페이로드면 이 assert가 깨진다."""
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"data", "meta"}
    meta = body["meta"]
    assert _UUID_RE.match(meta["trace_id"])
    assert meta["as_of"]


def _assert_error_envelope(body: dict) -> None:
    """§15.3 ApiError 포맷 — `error_code`/`message`/`trace_id`가 최상위.
    성공 봉투(`data`/`meta`)와 섞이지 않아야 한다."""
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert "data" not in body
    assert "meta" not in body


async def test_auth_register_response_is_enveloped(client):
    body, _ = await _register_user(client)
    _assert_success_envelope(body)
    assert "access_token" in body["data"]


async def test_users_me_response_is_enveloped(client):
    _, headers = await _register_user(client)

    response = await client.get("/users/me", headers=headers)

    assert response.status_code == 200
    body = response.json()
    _assert_success_envelope(body)
    assert UUID(body["data"]["user_id"])


async def test_users_approval_settings_response_is_enveloped(client):
    _, headers = await _register_user(client)

    response = await client.get("/users/me/approval-settings", headers=headers)

    assert response.status_code == 200
    _assert_success_envelope(response.json())


async def test_auth_error_response_is_the_apierror_envelope_not_success_shape(client):
    response = await client.post(
        "/auth/login", json={"email": _unique_email(), "password": "WrongPassword1!"}
    )

    assert response.status_code == 401
    _assert_error_envelope(response.json())


async def test_users_ownership_error_response_is_the_apierror_envelope(client):
    """users.py `_require_own_request`가 도메인 예외
    (ApprovalOwnershipError)를 던지고, 전역 핸들러가 AUTHZ_FORBIDDEN(403)
    ApiError로 변환하는지 실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.post("/users/me/approval-requests/999999/approve", headers=headers)

    assert response.status_code == 403
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "AUTHZ_FORBIDDEN"


async def test_exchange_credentials_not_found_error_response_is_the_apierror_envelope(client):
    """exchange_credentials.py는 이 리프에서 성공 응답을 아직 감싸지
    않지만(모듈 docstring 참조), 도메인 예외(CredentialNotFoundError)가
    전역 핸들러를 거쳐 RESOURCE_NOT_FOUND ApiError로 변환되는 것은 이번
    리프에서 이미 완성된 부분이라 실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.get("/exchange-credentials/bitget/balance", headers=headers)

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_marketplace_dispute_domain_error_response_is_the_apierror_envelope(client):
    """DisputeService.submit()이 DisputeError를 던지고, 전역 핸들러가
    VALIDATION_INVALID_FIELD(400) ApiError로 변환하는지 실호출로 확인한다
    (tests/integration/test_marketplace_router.py의 400 케이스와 동일 시나리오,
    여기서는 봉투 모양만 본다)."""
    _, headers = await _register_user(client)

    response = await client.post(
        "/marketplace/disputes",
        json={"purchase_id": 999999999, "reason": "사유"},
        headers=headers,
    )

    assert response.status_code == 400
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_strategy_builder_get_strategy_not_found_is_the_apierror_envelope(client):
    """StrategyBuilderService.get_strategy()가 존재하지 않는 전략에
    StrategyNotFoundError를 던지고, RESOURCE_NOT_FOUND(404)로 변환되는지
    확인한다 — get_strategy()가 저장 실패(400)와 같은
    StrategyLifecycleError를 공유했다면 이 케이스는 400으로 잘못 응답됐을
    것이다(라우터·exception_mapping.py 모듈 docstring 참조)."""
    _, headers = await _register_user(client)

    response = await client.get(
        "/strategy-builder/strategies/does-not-exist/1.0.0", headers=headers
    )

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_suitability_risk_profile_missing_is_the_apierror_envelope(client):
    """RiskProfileService.get_current()가 None이면 suitability.py가
    RiskProfileNotFoundError를 던지고 RESOURCE_NOT_FOUND(404)로 변환되는지
    확인한다."""
    _, headers = await _register_user(client)

    response = await client.get("/users/me/risk-profile", headers=headers)

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_alerts_cancel_not_found_error_response_is_the_apierror_envelope(client):
    """alerts.py PLT-20 — AlertNotFoundError가 전역 핸들러를 거쳐
    RESOURCE_NOT_FOUND ApiError로 변환되는지 실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.post("/alerts/999999999/cancel", headers=headers)

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_device_tokens_register_validation_error_response_is_the_apierror_envelope(client):
    """device_tokens.py PLT-20 — DeviceTokenError(알 수 없는 platform)가
    전역 핸들러를 거쳐 VALIDATION_INVALID_FIELD ApiError로 변환되는지
    실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "WindowsPhone"},
        headers=headers,
    )

    assert response.status_code == 400
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_device_tokens_deactivate_not_found_error_response_is_the_apierror_envelope(client):
    """device_tokens.py PLT-20 — DeviceTokenNotFoundError가 전역 핸들러를
    거쳐 RESOURCE_NOT_FOUND ApiError로 변환되는지 실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.delete("/device-tokens/999999999", headers=headers)

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_wallet_topup_validation_error_response_is_the_apierror_envelope(client):
    """wallet.py PLT-20 — WalletTopupError(0보다 크지 않은 금액)가 전역
    핸들러를 거쳐 VALIDATION_INVALID_FIELD ApiError로 변환되는지 실호출로
    확인한다. WalletTopupError는 PLT-108부터 이미 EXCEPTION_MAP에 있었으므로
    이 테스트는 라우터가 더 이상 자체 HTTPException으로 가로채지 않는다는
    것만 새로 고정한다."""
    _, headers = await _register_user(client)

    response = await client.post("/wallet/topup-requests", json={"amount": "0"}, headers=headers)

    assert response.status_code == 400
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_executions_create_without_credential_is_the_apierror_envelope(client):
    """executions.py PLT-19 — 연동되지 않은 거래소로 실행 생성을 시도하면
    `_available_balance`가 CredentialResolver.get_adapter()의
    CredentialNotFoundError를 그대로 propagate하고, 전역 핸들러가
    RESOURCE_NOT_FOUND(404) ApiError로 변환하는지 확인한다.
    CredentialNotFoundError는 PLT-17부터 이미 EXCEPTION_MAP에 있었으므로
    이 테스트는 라우터가 더 이상 자체 HTTPException으로 가로채지 않는다는
    것만 새로 고정한다."""
    _, headers = await _register_user(client)

    response = await client.post(
        "/executions",
        json={
            "strategy_id": "does-not-exist",
            "strategy_version": "1.0.0",
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )

    assert response.status_code == 404
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


async def test_auth_register_invalid_email_format_is_the_apierror_envelope(client):
    """SignupRequest.email이 `EmailStr`이므로 형식이 어긋난 입력은
    FastAPI가 `RequestValidationError`를 던지고, 전역 핸들러가
    VALIDATION_INVALID_FIELD(400) ApiError로 변환하는지 확인한다 —
    성공 봉투(`data`/`meta`) 모양이 아니라 검증 실패 자체를 명시적으로
    거부하는 불변식 테스트."""
    response = await client.post(
        "/auth/register", json={"email": "not-an-email", "password": STRONG_PASSWORD}
    )

    assert response.status_code == 400
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_auth_register_duplicate_email_is_the_apierror_envelope(client):
    """AuthService.signup()의 이메일 유일성 불변식 — 이미 등록된 이메일로
    재가입을 시도하면 AuthError가 전역 핸들러를 거쳐 AUTH_INVALID_CREDENTIALS
    (401) ApiError로 변환된다(계정열거 방지를 위해 별도 코드를 쓰지 않는다,
    exception_mapping.py 모듈 docstring 참조)."""
    email = _unique_email()
    first = await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert first.status_code == 201

    second = await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    assert second.status_code == 401
    body = second.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "AUTH_INVALID_CREDENTIALS"


async def test_users_me_without_authorization_header_is_the_apierror_envelope(client):
    """get_current_user()의 인증 필수 불변식 — Authorization 헤더가 없으면
    토큰 파싱 이전에 즉시 401 AUTH_REQUIRED ApiError로 거부되어야 한다."""
    response = await client.get("/users/me")

    assert response.status_code == 401
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "AUTH_REQUIRED"


async def test_users_me_with_garbage_bearer_token_is_the_apierror_envelope(client):
    """get_current_user()가 서명 검증에 실패하는 토큰을 그대로
    TokenVerifier.verify()에 넘기면 TokenInvalidError가 전역 핸들러를 거쳐
    AUTH_TOKEN_INVALID(401) ApiError로 변환되는지 확인한다 — 위조/손상된
    Bearer 토큰이 정상 응답으로 새어나가지 않는다는 불변식."""
    response = await client.get("/users/me", headers={"Authorization": "Bearer not-a-valid-jwt"})

    assert response.status_code == 401
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "AUTH_TOKEN_INVALID"


async def test_auth_register_unexpected_service_failure_is_generic_apierror_envelope(
    client, monkeypatch
):
    """실패주입 — AuthService.signup()이 분류되지 않은 예외(RuntimeError)를
    던지도록 monkeypatch하면, 전역 `Exception` 핸들러가 fail-closed로
    500 INTERNAL_ERROR ApiError를 돌려주고 원인 메시지("db-pool-exhausted"
    같은 내부 정보)를 응답 본문에 노출하지 않는지 확인한다
    (src/api/contracts/handlers.py `_handle_domain_or_unknown_exception`)."""
    from src.services.auth_service import AuthService

    async def _boom(self, email, password):
        raise RuntimeError("db-pool-exhausted")

    monkeypatch.setattr(AuthService, "signup", _boom)

    response = await client.post(
        "/auth/register",
        json={"email": _unique_email(), "password": STRONG_PASSWORD},
    )

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "db-pool-exhausted" not in body["message"]


async def test_portfolio_rebalance_empty_adjustments_is_the_apierror_envelope(client):
    """portfolio.py PLT-19 — PortfolioService.rebalance()가 빈 adjustments에
    RebalanceError를 던지고, 전역 핸들러가 VALIDATION_INVALID_FIELD(400)
    ApiError로 변환하는지 실호출로 확인한다."""
    _, headers = await _register_user(client)

    response = await client.post("/portfolio/rebalance", json={"adjustments": []}, headers=headers)

    assert response.status_code == 400
    body = response.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
