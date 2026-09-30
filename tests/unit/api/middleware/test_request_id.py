"""request_id 미들웨어 단위테스트 — 실제 main.py 앱을 띄우지 않고, 이
미들웨어 하나만 얹은 최소 FastAPI 앱으로 검증한다(main.py는 다른
세션이 활발히 편집 중이라 건드리지 않음 — 등록 자체는 PM이 처리)."""

import asyncio

import pytest
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

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("downstream handler failure")

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


# ------------------------------------------------------------------ #
#  Negative tests — invariant-violating input must not corrupt the   #
#  correlation-id contract (never-empty, per-request isolation).     #
# ------------------------------------------------------------------ #


async def test_empty_request_id_header_is_not_reused_as_is():
    """An empty `X-Request-ID` value must never propagate as the
    correlation id — a blank id in logs is indistinguishable from a
    missing one, so the middleware must regenerate instead of reusing it
    verbatim (falsy-string branch of `request.headers.get(...) or
    uuid.uuid4().hex`)."""
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo-request-id", headers={REQUEST_ID_HEADER: ""})

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated != ""
    assert response.json()["seen_inside_handler"] == generated


async def test_request_id_header_lookup_is_case_insensitive():
    """HTTP header names are case-insensitive (RFC 7230 §3.2); a client
    sending a lower-cased header must still have its id reused rather
    than silently discarded and regenerated."""
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/echo-request-id", headers={REQUEST_ID_HEADER.lower(): "lower-cased-id"}
        )

    assert response.headers[REQUEST_ID_HEADER] == "lower-cased-id"
    assert response.json()["seen_inside_handler"] == "lower-cased-id"


async def test_concurrent_requests_do_not_leak_request_id_across_contexts():
    """Two concurrent requests must never observe each other's
    correlation id — a shared/leaked contextvar would silently corrupt
    log correlation under load, which is exactly the failure mode the
    per-request `ContextVar.set`/`reset` pair exists to prevent."""
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_a, response_b = await asyncio.gather(
            client.get("/echo-request-id", headers={REQUEST_ID_HEADER: "request-a"}),
            client.get("/echo-request-id", headers={REQUEST_ID_HEADER: "request-b"}),
        )

    assert response_a.json()["seen_inside_handler"] == "request-a"
    assert response_b.json()["seen_inside_handler"] == "request-b"


# ------------------------------------------------------------------ #
#  Failure injection                                                 #
# ------------------------------------------------------------------ #


async def test_request_id_context_is_reset_after_downstream_exception():
    """If the wrapped handler raises, the `finally: request_id_var.reset`
    in `RequestIdMiddleware.dispatch` must still run — otherwise a failed
    request would leak its request id into whichever request (or
    background task) reuses the worker next."""
    transport = ASGITransport(app=_make_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="downstream handler failure"):
            await client.get("/boom")

    assert get_current_request_id() is None


# ------------------------------------------------------------------ #
#  Numeric performance assertion                                     #
# ------------------------------------------------------------------ #


@pytest.mark.perf
async def test_middleware_overhead_stays_within_budget(perf_budget):
    """The middleware only sets a contextvar and echoes a header, so its
    per-request overhead must stay well under a generous 50ms/request
    budget even for a small burst — a regression here (e.g. a blocking
    call sneaking into `dispatch`) would silently tax every request in
    the process."""
    transport = ASGITransport(app=_make_app())
    request_count = 20

    async def _make_requests():
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(request_count):
                response = await client.get("/echo-request-id")
                assert response.status_code == 200

    measured = await perf_budget.sample_async(_make_requests)
    per_request_seconds = measured.wall_ms / 1000 / request_count
    assert per_request_seconds < 0.05
