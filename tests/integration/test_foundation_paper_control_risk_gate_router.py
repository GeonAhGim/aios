"""FND-07/FND-06 라우터 통합테스트 — 실제 FastAPI 앱 + 실제 dev DB.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 배정 항목
검증:
① submit_paper_intent safety control 조회는 애플리케이션 레이어 테스트로
  이미 커버됨(tests/foundation/integration/paper_control) — 이 파일은
  ②/③이 실제 HTTP 경계에서도 동작하는지만 확인한다.
② kill switch 활성화가 RUNNING 배포를 실제로 PAUSED로 옮기는지(라우터가
  activate_safety_control() 뒤에 apply_safety_control_to_deployments()를
  잇는지).
③ 라우터의 예외->HTTP status 매핑 — 특히 start_deployment.py와
  pause_deployment.py가 이름은 같지만 서로 다른 InvalidDeploymentStateError
  클래스를 정의해, 라우터가 한쪽만 잡으면 나머지 절반이 500으로 새던 실제
  버그의 회귀 테스트.
"""
from __future__ import annotations

import uuid

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"

DEFAULT_RULES = {
    "max_total_exposure_pct": 80.0,
    "max_single_instrument_pct": 20.0,
    "min_cash_buffer_pct": 5.0,
    "max_daily_loss_pct": 3.0,
    "allowed_autonomy": "PAPER",
    "forbidden_assets": ["XYZ"],
}


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — task-1108이 mandates 라우터(이 파일이
        # _activate_mandate로 호출)의 raw HTTPException을 도메인 예외로
        # 교체했다. 도메인 예외는 이제 전역 Exception 핸들러(ServerErrorMiddleware
        # 승격)를 거치는데, Starlette가 정상 응답 뒤에도 예외를 재전파하기
        # 때문에 필요하다(test_auth_router.py client 픽스처와 동일 근거).
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
    # AuthService.issue_token()은 "sub"에 user_id를 담는다(auth_service.py) —
    # 서명 검증 없이 클레임만 꺼낸다(토큰 자체는 client가 방금 서버에서 발급
    # 받은 것이라 여기선 신뢰 문제가 없다).
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return {"Authorization": f"Bearer {token}"}, user_id


async def _activate_mandate(client, headers) -> None:
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )


async def _request_and_start_deployment(client, headers) -> str:
    key = uuid.uuid4().hex
    requested = (
        await client.post(
            "/v1/foundation/paper-deployments",
            json={
                "package_ref": "pkg-router-test",
                "adapter_type": "fake-paper-v1",
                "provider_sandbox_account_ref": "sandbox-acct-router",
                "endpoint_classification": "SANDBOX",
                "idempotency_key": f"req-{key}",
            },
            headers=headers,
        )
    ).json()
    deployment_id = requested["id"]
    started_response = await client.post(
        f"/v1/foundation/paper-deployments/{deployment_id}:start",
        json={"idempotency_key": f"start-{key}"},
        headers=headers,
    )
    assert started_response.status_code == 200, started_response.text
    assert started_response.json()["state"] == "RUNNING"
    return deployment_id


async def test_start_on_already_running_deployment_is_409_not_500(client):
    """회귀 — start_deployment.py의 InvalidDeploymentStateError가 pause_
    deployment.py의 동명 클래스와 달라 라우터가 못 잡던 버그(리뷰 중 발견,
    수정 전에는 여기서 500이 났다)."""
    headers, _ = await _register(client)
    await _activate_mandate(client, headers)
    deployment_id = await _request_and_start_deployment(client, headers)

    second_start = await client.post(
        f"/v1/foundation/paper-deployments/{deployment_id}:start",
        json={"idempotency_key": f"start-again-{uuid.uuid4().hex}"},
        headers=headers,
    )
    assert second_start.status_code == 409, second_start.text


async def test_self_service_kill_switch_pauses_running_deployment(client):
    """PM 배정 ② — self-service ACCOUNT kill switch가 RUNNING 배포를 실제로
    PAUSED로 옮기는지 HTTP 경계에서 확인한다."""
    headers, user_id = await _register(client)
    await _activate_mandate(client, headers)
    deployment_id = await _request_and_start_deployment(client, headers)

    kill_switch_response = await client.post(
        "/v1/foundation/risk-gate/safety-controls",
        json={"scope": "ACCOUNT", "scope_ref": user_id, "reason": "라우터 테스트 kill switch"},
        headers=headers,
    )
    assert kill_switch_response.status_code == 201, kill_switch_response.text

    listed = (
        await client.get("/v1/foundation/paper-deployments", headers=headers)
    ).json()["deployments"]
    matching = [d for d in listed if d["id"] == deployment_id]
    assert len(matching) == 1
    assert matching[0]["state"] == "PAUSED"
    assert matching[0]["fence_token"] == 1
