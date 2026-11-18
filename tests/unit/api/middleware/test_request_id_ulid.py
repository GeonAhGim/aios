"""ULID X-Request-ID must not break error envelopes (UAT-RID)."""

from __future__ import annotations

import re
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.contracts.envelope import current_trace_id
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
    async def ok() -> dict[str, str]:
        return {"trace": str(current_trace_id())}

    @app.get("/unauth")
    async def unauth() -> None:
        raise HTTPException(status_code=401, detail="no")

    return app


async def _get(path: str, rid: str | None) -> tuple[int, dict, str]:
    headers = {REQUEST_ID_HEADER: rid} if rid is not None else {}
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get(path, headers=headers)
    return r.status_code, r.json(), r.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(("path", "status"), [("/ok", 200), ("/unauth", 401), ("/nope", 404)])
async def test_ulid_header_keeps_status_and_echoes(path: str, status: int) -> None:
    code, body, echoed = await _get(path, ULID)
    assert code == status
    assert echoed == ULID
    if status == 401:
        assert UUID(str(body["trace_id"])) == request_id_to_uuid(ULID)


@pytest.mark.parametrize("bad", ["a" * 129, "has space in it", "abc", "8" + ULID[1:]])
async def test_invalid_header_is_not_echoed(bad: str) -> None:
    code, _, echoed = await _get("/unauth", bad)
    assert code == 401
    assert echoed != bad
    assert re.fullmatch(r"[0-9a-f]{32}", echoed)


def test_ulid_uuid_roundtrip_and_monotonic() -> None:
    a, b = "01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    ua, ub = request_id_to_uuid(a), request_id_to_uuid(b)
    assert ub.int == ua.int + 1
    assert request_id_to_uuid(a.lower()) == ua
    assert request_id_to_uuid("7" + "Z" * 25).int == (1 << 128) - 1


def test_validity_rules() -> None:
    assert is_valid_request_id("a" * 32)
    assert is_valid_request_id("12345678-1234-1234-1234-123456789abc")
    assert not is_valid_request_id("")
    assert not is_valid_request_id("U" + ULID[1:])  # U is not Crockford
    with pytest.raises(ValueError):
        request_id_to_uuid("abc")


def test_current_trace_id_defensive_on_bad_value() -> None:
    token = request_id_var.set("not valid!")
    try:
        assert isinstance(current_trace_id(), UUID)
    finally:
        request_id_var.reset(token)
