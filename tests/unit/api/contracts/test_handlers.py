"""install_exception_handlers() 통합테스트 — main.py 앱을 띄우지 않고
최소 FastAPI 앱으로 검증한다(main.py는 다른 세션이 활발히 편집 중이라
건드리지 않음 — 실제 등록은 이미 main.py에 완료돼 있고, 여기선 이
함수 자체의 동작만 확인)."""
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from src.api.contracts.handlers import install_exception_handlers
from src.services.auth_service import AuthError


class _Body(BaseModel):
    name: str


def _make_app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/validate")
    async def validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/raw-http-exception")
    async def raw_http_exception() -> None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않습니다")

    @app.get("/structured-http-exception")
    async def structured_http_exception() -> None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"error_code": "VALIDATION_IDEMPOTENCY_KEY_REQUIRED", "message": "헤더 필요"},
        )

    @app.get("/domain-exception")
    async def domain_exception() -> None:
        raise AuthError("이메일 또는 비밀번호가 올바르지 않습니다.")

    @app.get("/unmapped-exception")
    async def unmapped_exception() -> None:
        raise RuntimeError("DB 연결 실패, 비밀번호는 hunter2")

    return app


async def test_request_validation_error_returns_envelope_with_field_names():
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate", json={})

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "trace_id" in body
    assert "name" in body["details"]["fields"][0]


async def test_raw_http_exception_gets_status_based_default_code():
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/raw-http-exception")

    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "존재하지 않습니다"


async def test_structured_http_exception_detail_is_used_as_is():
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/structured-http-exception")

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"
    assert body["message"] == "헤더 필요"


async def test_domain_exception_maps_through_exception_mapping():
    # 500이 아닌 도메인 예외도 Exception 핸들러 하나로 잡히므로(핸들러
    # 등록 대상이 Exception 자체라 Starlette가 이 경로를 raise_app_
    # exceptions로 다시 올린다), 여기서도 꺼야 한다.
    transport = ASGITransport(app=_make_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/domain-exception")

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "AUTH_INVALID_CREDENTIALS"


async def test_unmapped_exception_hides_internal_message_but_keeps_trace_id():
    """원인 메시지("DB 연결 실패, 비밀번호는 hunter2")가 클라이언트에게
    그대로 노출되면 안 된다 — error 로그에만 남고, 응답은 고정
    메시지 + trace_id.

    raise_app_exceptions=False가 필요한 이유: httpx의 ASGITransport는
    핸들러가 잡아 정상 500 응답을 만들었어도 기본값(True)이면 테스트
    프로세스 쪽으로 원본 예외를 다시 던진다 — 실제 서버 동작과 무관한
    테스트 클라이언트만의 편의 기능이라 여기서는 꺼야 실제로 핸들러가
    만든 응답을 확인할 수 있다."""
    transport = ASGITransport(app=_make_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/unmapped-exception")

    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert "trace_id" in body
