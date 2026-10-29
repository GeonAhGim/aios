"""PLT-17~21: DB 의존성 없는 응답 봉투 핸들러 계약 (task-18588).

미분류 예외 정보 비노출, 성공/실패 봉투, 검증 오류와 HTTP 상태 매핑을 검증한다.
Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.handlers import install_exception_handlers


class _UnclassifiedHandlerFailure(RuntimeError):
    """EXCEPTION_MAP 어디에도 없는, 서비스 계층에서 올라올 법한 미분류 예외."""


class _ValidatedBody(BaseModel):
    count: int


def _build_bare_envelope_app() -> FastAPI:
    bare_app = FastAPI()
    install_exception_handlers(bare_app)

    @bare_app.get("/ok")
    async def _ok_endpoint() -> ApiResponse[dict[str, int]]:
        return ok({"value": 1})

    @bare_app.get("/boom")
    async def _boom_endpoint() -> None:
        raise _UnclassifiedHandlerFailure(
            "connection string: postgresql://admin:hunter2@internal-db/prod"
        )

    @bare_app.get("/legacy-not-found")
    async def _legacy_not_found_endpoint() -> None:
        raise HTTPException(status_code=404, detail="widget xyz not found")

    @bare_app.post("/validate")
    async def _validate_endpoint(body: _ValidatedBody) -> None:
        return None

    return bare_app


@pytest.fixture
async def bare_client() -> AsyncGenerator[AsyncClient]:
    bare_app = _build_bare_envelope_app()
    transport = ASGITransport(app=bare_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_unclassified_exception_envelope_leaks_no_message_type_or_traceback(
    bare_client: AsyncClient,
) -> None:
    """(a) 미분류 예외는 표준 오류 봉투로 반환되고, 응답 본문(raw text
    포함)에는 원본 예외 메시지도, 예외 타입 이름도, 트레이스백도 남지
    않는다 — fail-closed 고정 메시지 + INTERNAL_ERROR만 노출한다."""
    response = await bare_client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["details"] == {}

    raw_text = response.text
    for leaked in (
        "hunter2",
        "postgresql://",
        "_UnclassifiedHandlerFailure",
        "Traceback",
        "internal-db",
    ):
        assert leaked not in body["message"]
        assert leaked not in raw_text


async def test_envelope_keys_present_on_both_success_and_error_paths(bare_client):
    """(b) 성공 응답은 §2.3 `ApiResponse`(data/meta.trace_id/meta.as_of)
    모양을, 실패 응답은 §15.3 `ApiError`(error_code/message/details/
    trace_id/retry_after_seconds) 모양을 실제로 갖춘다 — 어느 한쪽이라도
    봉투 필드가 빠지면(예: data 없이 raw dict만 반환) 이 단언이 잡는다."""
    ok_response = await bare_client.get("/ok")
    assert ok_response.status_code == 200
    ok_body = ok_response.json()
    assert set(ok_body.keys()) == {"data", "meta"}
    assert ok_body["data"] == {"value": 1}
    assert set(ok_body["meta"].keys()) >= {"trace_id", "as_of"}

    error_response = await bare_client.get("/boom")
    assert error_response.status_code == 500
    error_body = error_response.json()
    assert set(error_body.keys()) == {
        "error_code",
        "message",
        "details",
        "trace_id",
        "retry_after_seconds",
    }


async def test_validation_error_is_enveloped_with_validation_invalid_field_code(bare_client):
    """(c-1) pydantic 바디 검증 실패(`RequestValidationError`)는 raw
    FastAPI 422 기본 형식이 아니라 §3.3 VALIDATION_INVALID_FIELD + 400으로
    봉투화된다."""
    response = await bare_client.post("/validate", json={"count": "not-an-int"})

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"]["fields"] == ["body.count"]


async def test_legacy_http_exception_with_mapped_status_uses_matching_error_code(bare_client):
    """(c-2) `_STATUS_DEFAULT_CODE`에 있는 상태코드(404)로 레거시
    `HTTPException`이 발생하면 대응하는 RESOURCE_NOT_FOUND로 봉투화되고,
    라우터가 고른 상태코드(404)와 원본 detail 메시지가 그대로 보존된다."""
    response = await bare_client.get("/legacy-not-found")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "widget xyz not found"
