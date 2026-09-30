"""PLT-05 middleware rejection and failure-injection regression tests."""

import time
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware import request_context
from src.api.middleware.request_id import RequestIdMiddleware
from src.core.logging.request_context import request_id_var


@pytest.mark.parametrize(
    "traceparent",
    [
        "00-" + "0" * 32 + "-0123456789abcdef-01",
        "00-" + "g" * 32 + "-0123456789abcdef-01",
        "00-0123456789abcdef-0123456789abcdef-01",
    ],
    ids=["negative-zero-trace", "negative-nonhex-trace", "negative-short-trace"],
)
def test_negative_invalid_traceparent_is_rejected(monkeypatch, traceparent):
    replacement = UUID("12345678-1234-4234-8234-123456789abc")
    generator = Mock(return_value=replacement)
    monkeypatch.setattr(request_context.uuid, "uuid4", generator)

    assert request_context._extract_trace_id(traceparent) == replacement
    generator.assert_called_once_with()


def test_valid_traceparent_is_preserved():
    trace = "12345678123442348234123456789abc"
    assert request_context._extract_trace_id(
        f"00-{trace}-0123456789abcdef-01"
    ) == UUID(hex=trace)


async def test_failure_injection_restores_outer_request_context(monkeypatch):
    middleware = RequestIdMiddleware(AsyncMock())
    request = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"x-request-id", b"inner-request")],
    })

    async def fail(_request):
        assert request_id_var.get() == "inner-request"
        raise RuntimeError("injected downstream failure")

    dependency = AsyncMock()
    monkeypatch.setattr(dependency, "handle", fail)
    token = request_id_var.set("outer-request")
    try:
        with pytest.raises(RuntimeError, match="injected downstream failure"):
            await middleware.dispatch(request, dependency.handle)
        assert request_id_var.get() == "outer-request"
        response = await middleware.dispatch(
            request, AsyncMock(return_value=Response(status_code=204))
        )
        assert response.status_code == 204
        assert response.headers["X-Request-ID"] == "inner-request"
        assert request_id_var.get() == "outer-request"
    finally:
        request_id_var.reset(token)


@pytest.mark.perf
async def test_request_id_middleware_p95_overhead():
    """PLT platform spec: middleware p95 overhead below 1 ms."""
    middleware = RequestIdMiddleware(AsyncMock())
    request = Request({
        "type": "http", "method": "GET", "path": "/", "headers": [],
    })

    async def respond(_request):
        return Response(status_code=204)

    for _ in range(20):
        await middleware.dispatch(request, respond)
    samples = []
    for _ in range(1000):
        started = time.perf_counter()
        await respond(request)
        baseline = time.perf_counter() - started
        started = time.perf_counter()
        response = await middleware.dispatch(request, respond)
        samples.append(max(0.0, time.perf_counter() - started - baseline))
        assert response.status_code == 204
    assert sorted(samples)[949] < 0.001
