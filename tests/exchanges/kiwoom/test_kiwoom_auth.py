"""KiwoomAuthClient — OAuth2 token cache/refresh + signed request transport.

BR-23(task-7569), exchange onboarding step (b). Mirrors
`tests/unit/exchanges/test_kis_auth.py`'s `httpx.MockTransport` fake-transport
pattern -- no real network call, no real wall-clock wait (a fake `sleep_fn`
is injected everywhere `ResilientTransport`/`TokenBucket` would otherwise
sleep).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.kiwoom.auth import (
    KiwoomAccountType,
    KiwoomAuthClient,
    build_token_bucket,
    reset_token_bucket_registry_for_test,
)


async def _fast_sleep(seconds: float) -> None:
    return None


def _client(handler, *, is_paper_trading: bool = True) -> KiwoomAuthClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://mockapi.kiwoom.com", transport=transport)
    return KiwoomAuthClient(
        "app",
        "secret",
        "1234567890",
        is_paper_trading=is_paper_trading,
        http_client=http_client,
        sleep_fn=_fast_sleep,
    )


@pytest.fixture(autouse=True)
def _clean_bucket_registry():
    reset_token_bucket_registry_for_test()
    yield
    reset_token_bucket_registry_for_test()


def _token_response(token: str = "tok-1", expires: float = 7200) -> httpx.Response:  # noqa: S107
    return httpx.Response(200, json={"token": token, "expires_dt_seconds": expires})


# ---- token cache: hit / miss / refresh-on-expiry ----


async def test_ensure_token_fetches_once_and_caches_hit():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _token_response()

    client = _client(handler)
    token1 = await client._ensure_token()
    token2 = await client._ensure_token()

    assert token1 == "tok-1"
    assert token2 == "tok-1"
    assert call_count == 1  # cache hit -- second call did not refetch


async def test_ensure_token_refreshes_after_invalidate():
    """miss / refresh-on-expiry -- `_invalidate_token()` forces the next
    `_ensure_token()` to refetch (this is the same primitive `_request` uses
    internally after a 401, exercised directly here)."""
    tokens = iter(["tok-1", "tok-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        return _token_response(next(tokens))

    client = _client(handler)
    first = await client._ensure_token()
    client._invalidate_token()
    second = await client._ensure_token()

    assert first == "tok-1"
    assert second == "tok-2"


async def test_ensure_token_concurrent_calls_fetch_once():
    """Double-checked locking -- concurrent callers arriving while the token
    is expired issue exactly one token-issuance call."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _token_response()

    client = _client(handler)
    results = await asyncio.gather(*[client._ensure_token() for _ in range(5)])

    assert results == ["tok-1"] * 5
    assert call_count == 1


# ---- negative (1/3): HTTP 401 -> typed error, with one auth-retry ----


async def test_request_retries_once_after_401_then_succeeds():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls.append("token")
            return _token_response(f"tok-{calls.count('token')}")
        calls.append("api")
        if calls.count("api") == 1:
            return httpx.Response(401, json={"return_code": 1, "return_msg": "expired"})
        return httpx.Response(200, json={"return_code": 0, "output": {}})

    client = _client(handler)
    result = await client._request(
        "GET", "/uapi/domestic-stock/v1/market-data/ticker", "KW_TICKER"
    )

    assert result == {"return_code": 0, "output": {}}
    assert calls.count("token") == 2  # first token + refreshed token after the 401
    assert calls.count("api") == 2


async def test_request_surfaces_second_401_as_fatal_without_looping_forever():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        return httpx.Response(401, json={"return_code": 1})

    client = _client(handler)
    # A second consecutive 401 (after the one auth-retry already used) is not
    # retried again -- AUTH is not a retryable kind, so it surfaces fatally.
    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/x", "KW_TICKER")


# ---- negative (2/3): HTTP 5xx -> typed error ----


async def test_request_5xx_exhausts_retries_and_raises_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        return httpx.Response(500, text="internal error")

    client = _client(handler)
    with pytest.raises(RetryableExchangeError):
        await client._request("GET", "/x", "KW_TICKER")


# ---- negative (3/3): malformed payload rejected ----


async def test_fetch_token_rejects_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = _client(handler)
    with pytest.raises(FatalExchangeError):
        await client._ensure_token()


async def test_fetch_token_rejects_missing_token_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_dt_seconds": 3600})

    client = _client(handler)
    with pytest.raises(FatalExchangeError):
        await client._ensure_token()


def test_classify_body_rejects_non_zero_return_code():
    client = _client(lambda request: httpx.Response(200, json={}))
    response = httpx.Response(200, json={"return_code": 9, "return_msg": "denied"})
    error = client._classify_body(response)
    assert error is not None
    assert error.kind is ExchangeErrorKind.UNKNOWN_RESPONSE


def test_classify_body_rejects_non_object_body():
    client = _client(lambda request: httpx.Response(200, json={}))
    response = httpx.Response(200, json=[1, 2, 3])
    error = client._classify_body(response)
    assert error is not None
    assert error.kind is ExchangeErrorKind.UNKNOWN_RESPONSE


# ---- rate limit exceeded: waits / raises ----


async def test_rate_limit_bucket_waits_when_exhausted():
    waits: list[float] = []

    async def counting_sleep(seconds: float) -> None:
        waits.append(seconds)

    bucket = build_token_bucket(KiwoomAccountType.PAPER, sleep=counting_sleep)
    # PAPER burst/rate == 2.0 -- the third immediate acquire must wait for refill.
    await bucket.acquire(timeout=10.0)
    await bucket.acquire(timeout=10.0)
    await bucket.acquire(timeout=10.0)

    assert waits  # at least one call absorbed a wait instead of failing immediately


async def test_rate_limit_bucket_raises_when_wait_exceeds_timeout():
    bucket = build_token_bucket(KiwoomAccountType.PAPER, sleep=_fast_sleep)
    await bucket.acquire(timeout=10.0)
    await bucket.acquire(timeout=10.0)

    with pytest.raises(ExchangeError) as excinfo:
        await bucket.acquire(n=1, timeout=0.0)
    assert excinfo.value.kind is ExchangeErrorKind.RATE_LIMITED


# ---- failure-injection: the transport itself raises ----


async def test_request_wraps_transport_connect_error_as_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(RetryableExchangeError):
        await client._request("GET", "/x", "KW_TICKER")
