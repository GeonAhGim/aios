"""request_id 미들웨어 단위테스트 — 실제 main.py 앱을 띄우지 않고, 이
미들웨어 하나만 얹은 최소 FastAPI 앱으로 검증한다(main.py는 다른
세션이 활발히 편집 중이라 건드리지 않음 — 등록 자체는 PM이 처리)."""
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from src.core.logging.request_context import get_current_request_id


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo-request-id")
    async def echo_request_id() -> dict[str, str | None]:
        return {"seen_inside_handler": get_current_request_id()}

    return app


async def test_generates_request_id_when_header_absent():
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo-request-id")

    assert response.status_code == 200
    generated = response.headers[REQUEST_ID_HEADER]
    assert generated
    assert response.json()["seen_inside_handler"] == generated


async def test_reuses_client_supplied_request_id_header():
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/echo-request-id", headers={REQUEST_ID_HEADER: "client-supplied-id"}
        )

    assert response.headers[REQUEST_ID_HEADER] == "client-supplied-id"
    assert response.json()["seen_inside_handler"] == "client-supplied-id"


async def test_request_id_not_visible_outside_request_context():
    assert get_current_request_id() is None
