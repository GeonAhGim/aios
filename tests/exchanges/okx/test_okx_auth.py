"""task-7595(BR-21b) -- OKX auth/signing + rate-limited HTTP client tests.

D2 floor: negative tests >= 3, one failure-injection test, one numeric
performance assertion (ADR-2026-09-09-C Decision 1) -- evidenced inline
below.
"""

from __future__ import annotations

import time

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.okx.auth import (
    OKXHTTPClient,
    build_headers,
    iso_timestamp_from_epoch_ms,
    sign_request,
)

# ---- DoD 1: fixed input -> fixed signature (>= 3 assertions) ----


def test_sign_request_fixed_input_get_no_body():
    """Independently computed via Python's own hmac/hashlib/base64 outside
    this codebase (see task notes) -- a fixed input must always reproduce
    this exact signature, or the signing function has silently changed."""
    sig = sign_request(
        "mysecretkey", "2020-12-08T09:08:57.715Z", "GET", "/api/v5/account/balance"
    )
    assert sig == "RVdxQo0JNusP1O6Zsu6zd9fqQpRvDApYjcSPk8hKqYQ="


def test_sign_request_fixed_input_post_with_body():
    sig = sign_request(
        "mysecretkey",
        "2020-12-08T09:08:57.715Z",
        "POST",
        "/api/v5/trade/order",
        '{"instId":"BTC-USDT","sz":"1"}',
    )
    assert sig == "+KBSzCCMhTFU/hS/S6uAjpGyU/wqOl7MtjjKPqIS6EY="


def test_sign_request_fixed_input_different_secret_and_query_string():
    sig = sign_request(
        "anothersecret999",
        "2026-01-01T00:00:00.000Z",
        "GET",
        "/api/v5/market/ticker?instId=BTC-USDT",
    )
    assert sig == "+4ntefsNHMnRGivwBsHAVtkcnDIqKcxj0KaaRKfK7IU="


def test_iso_timestamp_from_epoch_ms_matches_okx_format():
    assert iso_timestamp_from_epoch_ms(1607418537715) == "2020-12-08T09:08:57.715Z"


# ---- build_headers ----


def test_build_headers_demo_mode_adds_simulated_trading_header():
    headers = build_headers(
        "key",
        "secret",
        "phrase",
        timestamp="2020-12-08T09:08:57.715Z",
        method="GET",
        request_path="/api/v5/account/balance",
        demo_mode=True,
    )
    assert headers["x-simulated-trading"] == "1"
    assert headers["OK-ACCESS-KEY"] == "key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "phrase"


def test_build_headers_live_mode_omits_simulated_trading_header():
    headers = build_headers(
        "key",
        "secret",
        "phrase",
        timestamp="2020-12-08T09:08:57.715Z",
        method="GET",
        request_path="/api/v5/account/balance",
        demo_mode=False,
    )
    assert "x-simulated-trading" not in headers


# ---- negative / failure-injection tests (D2 floor: negative >= 3, 1 failure-injection) ----


def _client(handler, *, sleep_fn=None, rng=None) -> OKXHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://www.okx.com", transport=transport, timeout=10.0
    )
    return OKXHTTPClient(
        "key", "secret", "phrase", http_client=http_client, sleep_fn=sleep_fn, rng=rng
    )


async def test_request_raises_fatal_on_body_level_error_code():
    """Negative 1: a non-'0' top-level body code (invalid signature, 50113)
    must fail closed as fatal, not be treated as success."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "50113", "msg": "invalid signature", "data": []})

    client = _client(handler)
    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/api/v5/account/balance")


async def test_request_raises_retryable_on_non_json_response():
    """Negative 2: a non-JSON 200 response body is treated as a retryable
    transport issue, not silently parsed as empty/successful."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _client(handler)
    with pytest.raises(RetryableExchangeError):
        await client._request("GET", "/api/v5/account/balance")


async def test_request_raises_fatal_immediately_on_400_without_retry():
    """Negative 3: a plain 4xx (not 429) is fatal immediately -- no retry
    attempts wasted on a request that will never succeed unchanged."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    client = _client(handler)
    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/api/v5/account/balance")
    assert calls["n"] == 1


async def test_request_retries_429_then_succeeds_with_exponential_backoff():
    """Failure-injection: HTTP 429 (rate limited) is retried with
    exponential backoff (task spec's "429 exponential backoff" DoD),
    succeeding once the venue stops rate-limiting."""
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": [{"ok": True}]})

    client = _client(handler, sleep_fn=fake_sleep, rng=lambda: 1.0)
    result = await client._request("GET", "/api/v5/account/balance")

    assert result["data"] == [{"ok": True}]
    assert calls["n"] == 3
    assert sleep_calls == [0.5, 1.0]  # exponential backoff (base=0.5, deterministic rng==1.0)


async def test_request_treats_okx_body_level_rate_limit_code_as_retryable():
    """Negative 4: OKX can report rate-limiting via body code 50011 with an
    HTTP 200 wrapper -- this must raise `RetryableExchangeError` (signaling
    the caller it may retry), not `FatalExchangeError`, even though
    `ResilientTransport` only auto-retries HTTP-status-level failures and
    evaluates a body-level classification once (module docstring of
    `src/exchanges/common/transport.py`, §5.4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "50011", "msg": "too many requests", "data": []})

    client = _client(handler)
    with pytest.raises(RetryableExchangeError):
        await client._request("GET", "/api/v5/account/balance")


# ---- numeric performance assertion ----


@pytest.mark.perf
def test_sign_request_latency_budget():
    """Numeric performance assertion: pure signing (no network) must average
    under 0.1ms per call across 1000 calls -- a regression guard on the
    mixin's own CPU overhead, not a live-venue round-trip budget."""
    start = time.perf_counter()
    for _ in range(1000):
        sign_request("secret", "2020-12-08T09:08:57.715Z", "GET", "/api/v5/account/balance")
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.0001
