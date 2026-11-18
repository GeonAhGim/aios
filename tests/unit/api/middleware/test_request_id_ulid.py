"""ULID X-Request-ID must be accepted and mapped to a UUID trace_id (UAT-RID regression)."""

from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.contracts.envelope import current_trace_id, ok
from src.api.contracts.handlers import install_exception_handlers
from src.api.middleware.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from src.core.logging.request_context import request_id_var
from src.core.logging.request_id_format import is_valid_request_id, request_id_to_uuid

ULID = "01M7PM0JK2XG3E6P15WD6C7DKN"


def _app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def _ok() -> object:
        return ok({"x": 1})

    @app.get("/unauth")
    async def _unauth() -> None:
        raise HTTPException(status_code=401, detail="no")

    return app


async def _get(path: str, rid: str | None) -> tuple[int, dict, str]:
    headers = {REQUEST_ID_HEADER: rid} if rid is not None else {}
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get(path, headers=headers)
    return r.status_code, r.json(), r.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(("path", "status"), [("/ok", 200), ("/unauth", 401), ("/missing", 404)])
async def test_ulid_header_keeps_status_and_echoes(path: str, status: int):
    code, body, echoed = await _get(path, ULID)
    assert code == status
    assert echoed == ULID
    if path != "/missing":  # unrouted 404 is Starlette's own body, not an envelope
        trace = body["meta"]["trace_id"] if status == 200 else body["trace_id"]
        assert UUID(trace) == request_id_to_uuid(ULID)


@pytest.mark.parametrize(
    "bad", ["a" * 129, "has space 01M7PM0JK2XG3E6P15WD6C7DKN", "abc", "81M7PM0JK2XG3E6P15WD6C7DKN"]
)
async def test_invalid_ids_not_echoed(bad: str):
    assert not is_valid_request_id(bad)
    code, _, echoed = await _get("/ok", bad)
    assert code == 200
    assert echoed != bad
    assert len(echoed) == 32
    UUID(hex=echoed)


def test_ulid_uuid_roundtrip_and_monotonic():
    u = request_id_to_uuid(ULID)
    assert request_id_to_uuid(ULID.lower()) == u
    assert u.int < request_id_to_uuid("7ZZZZZZZZZZZZZZZZZZZZZZZZZ").int
    assert request_id_to_uuid("00000000000000000000000001").int == 1
    a, b = "01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    assert a < b and request_id_to_uuid(a) < request_id_to_uuid(b)
    h = "0123456789abcdef0123456789abcdef"
    assert request_id_to_uuid(h) == UUID(hex=h)


def test_unconvertible_id_yields_fresh_uuid_not_exception():
    with pytest.raises(ValueError):
        request_id_to_uuid("not-a-request-id")
    token = request_id_var.set("not-a-request-id")
    try:
        assert isinstance(current_trace_id(), UUID)
    finally:
        request_id_var.reset(token)
