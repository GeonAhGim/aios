"""PLT-API-204 라우터 계약 테스트 — CH-5 `DELETE` 엔드포인트의 204 응답에
본문이 없음을 증명한다.

FastAPI>=0.116은 204 응답에 `response_model`이 남아 있으면(반환 타입 추론
결과가 truthy) 라우트 등록 시점에 `AssertionError("Status code 204 must
not have a response body")`로 죽는다 — `src/api/routers/charting.py`의
두 `DELETE` 데코레이터가 이를 `response_model=None`으로 명시해 피한다.
이 테스트는 라우터 import가 죽지 않는다는 사실(수집 성공)과 더불어, 실제
HTTP 응답이 계약대로 빈 본문임을 실행 시점에도 확인한다."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.conftest import lifespan_context_with_retry

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/charting"


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _auth_headers(client: AsyncClient) -> dict:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def test_delete_layout_and_indicator_template_return_204_with_no_body(
    client: AsyncClient,
) -> None:
    headers = await _auth_headers(client)

    layout = await client.post(
        f"{BASE}/layouts",
        json={"name": "layout-1", "layout_state": {}},
        headers=headers,
    )
    assert layout.status_code == 201
    layout_id = layout.json()["data"]["id"]

    template = await client.post(
        f"{BASE}/indicator-templates",
        json={"name": "template-1", "template": {}},
        headers=headers,
    )
    assert template.status_code == 201
    template_id = template.json()["data"]["id"]

    delete_layout_response = await client.delete(f"{BASE}/layouts/{layout_id}", headers=headers)
    delete_template_response = await client.delete(
        f"{BASE}/indicator-templates/{template_id}", headers=headers
    )

    for response in (delete_layout_response, delete_template_response):
        assert response.status_code == 204
        assert response.content == b""
        assert "content-length" not in response.headers or response.headers["content-length"] == "0"
