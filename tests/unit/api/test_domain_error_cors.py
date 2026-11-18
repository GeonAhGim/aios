"""도메인 예외 응답에도 CORS 헤더가 붙는다 (UAT 첫 기동에서 발견, 2026-11-18).

Starlette는 `Exception` 핸들러를 모든 미들웨어 바깥에서 실행해 도메인 예외(401 등) 응답이
CORSMiddleware를 거치지 않았다. 별도 오리진 프런트는 오류 본문을 못 읽고 'Failed to fetch'만 보았다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.api.contracts.handlers import install_exception_handlers
from src.services.auth_service import AuthError

ORIGIN = "http://127.0.0.1:18080"


def _app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/domain")
    async def domain() -> None:
        raise AuthError("이메일 또는 비밀번호가 올바르지 않습니다.")

    @app.get("/unknown")
    async def unknown() -> None:
        raise RuntimeError("boom")

    @app.get("/http")
    async def http() -> None:
        raise HTTPException(status_code=404, detail="없음")

    @app.get("/stream-fail")
    async def stream_fail() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("mid-stream")

        return StreamingResponse(gen())

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_domain_error_response_carries_cors_headers(client: TestClient) -> None:
    resp = client.get("/domain", headers={"Origin": ORIGIN})
    assert resp.status_code == 401
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.json()["error_code"] == "AUTH_INVALID_CREDENTIALS"


def test_unknown_error_is_500_envelope_with_cors_and_no_leak(client: TestClient) -> None:
    resp = client.get("/unknown", headers={"Origin": ORIGIN})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert "boom" not in resp.text
    assert resp.json()["error_code"] == "INTERNAL_ERROR"


def test_disallowed_origin_gets_no_cors_header(client: TestClient) -> None:
    resp = client.get("/domain", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 401
    assert "access-control-allow-origin" not in resp.headers


def test_http_exception_path_unchanged(client: TestClient) -> None:
    resp = client.get("/http", headers={"Origin": ORIGIN})
    assert resp.status_code == 404
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_error_after_response_started_is_not_swallowed(client: TestClient) -> None:
    """스트리밍이 시작된 뒤의 예외는 봉투로 바꿀 수 없다 -- 응답이 끊기거나 예외가 전파돼야 한다."""
    try:
        resp = client.get("/stream-fail", headers={"Origin": ORIGIN})
    except Exception:  # noqa: BLE001 - 전파되면 정상(끊긴 응답)
        return
    assert b"error_code" not in resp.content
