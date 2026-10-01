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
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.foundation_deps import get_charting_repository
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


# ── 부정 테스트 1: 이름이 빈 문자열이면 400 검증 오류를 반환한다 ──────────────
# 불변식: CreateChartLayoutRequest.name 은 min_length=1 (contracts/v1.py).


async def test_create_layout_rejects_empty_name(client: AsyncClient) -> None:
    headers = await _auth_headers(client)
    response = await client.post(
        f"{BASE}/layouts",
        json={"name": "", "layout_state": {}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


# ── 부정 테스트 2: 이름이 120자를 초과하면 400 검증 오류를 반환한다 ───────────
# 불변식: name 필드는 max_length=120을 초과할 수 없다 (contracts/v1.py).


async def test_create_layout_rejects_name_exceeding_max_length(client: AsyncClient) -> None:
    headers = await _auth_headers(client)
    long_name = "x" * 121
    response = await client.post(
        f"{BASE}/layouts",
        json={"name": long_name, "layout_state": {}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


# ── 부정 테스트 3: 템플릿 이름이 빈 문자열이면 400 검증 오류를 반환한다 ───────
# 불변식: CreateChartIndicatorTemplateRequest.name 도 min_length=1.


async def test_create_template_rejects_empty_name(client: AsyncClient) -> None:
    headers = await _auth_headers(client)
    response = await client.post(
        f"{BASE}/indicator-templates",
        json={"name": "", "template": {}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


class _BoomChartingRepository:
    """create_layout 호출 시 예외를 던져 저장소 장애를 흉내낸다."""

    async def create_layout(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        layout_state: dict[str, Any],
    ) -> None:
        raise RuntimeError("simulated repository crash")


# ── 실패주입 1: 저장소 예외 시 500 Internal Server Error 를 반환한다 ──────────
# create_layout()이 예외를 raise 하면 전역 핸들러(exception_mapping.py)가
# 번역 없는 예외를 INTERNAL_ERROR(500) 봉투로 fail-closed 처리해야 한다.


async def test_create_layout_returns_500_when_repository_raises(
    client: AsyncClient,
) -> None:
    headers = await _auth_headers(client)
    app.dependency_overrides[get_charting_repository] = _BoomChartingRepository
    try:
        response = await client.post(
            f"{BASE}/layouts",
            json={"name": "fault-injected-layout", "layout_state": {}},
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_charting_repository, None)

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "data" not in body, "장애 상황에서 레이아웃 데이터가 노출되면 안 된다(fail-closed)"
