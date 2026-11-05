"""task-7600 -- Binance signing (fixed vectors) + 429/418 handling."""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.binance.auth import BinanceHTTPClient, build_signed_query, sign_query

# Vector from the public Binance Spot API docs (SIGNED endpoint example).
_DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
_DOC_QUERY = (
    "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
    "&price=0.1&recvWindow=5000&timestamp=1499827319559"
)
_DOC_SIG = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_sign_query_matches_doc_vector():
    assert sign_query(_DOC_SECRET, _DOC_QUERY) == _DOC_SIG


def test_sign_query_fixed_values():
    assert sign_query("k", "a=1") == sign_query("k", "a=1")
    assert sign_query("k", "a=1") != sign_query("k", "a=2")
    assert sign_query("k", "a=1") != sign_query("k2", "a=1")
    assert len(sign_query("k", "")) == 64


def test_build_signed_query_orders_params_and_signs_prefix():
    q = build_signed_query("sec", {"symbol": "BTCUSDT"}, timestamp_ms=1700000000000)
    prefix, sig = q.rsplit("&signature=", 1)
    assert prefix == "symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000"
    assert sig == sign_query("sec", prefix)


def _client(handler, sleeps):
    async def sleep(d):
        sleeps.append(d)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x.test")
    return BinanceHTTPClient("key", "sec", http_client=http, sleep_fn=sleep, rng=lambda: 0.0)


async def test_signed_request_carries_signature_and_api_key():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers["X-MBX-APIKEY"]
        return httpx.Response(200, json={"ok": 1})

    c = _client(handler, [])
    assert await c._request("GET", "/api/v3/order", params={"symbol": "BTCUSDT"}) == {"ok": 1}
    assert "signature=" in seen["url"] and "timestamp=" in seen["url"]
    assert seen["key"] == "key"


async def test_public_request_is_not_signed():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={})

    await _client(handler, [])._request("GET", "/api/v3/ping")
    assert "signature" not in seen["url"]


async def test_429_backs_off_then_succeeds():
    calls = []
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": 1})

    assert await _client(handler, sleeps)._request("GET", "/api/v3/ping") == {"ok": 1}
    assert len(calls) == 3 and len(sleeps) == 2


async def test_418_is_not_retried_and_is_retryable_error():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(418)

    with pytest.raises(RetryableExchangeError):
        await _client(handler, [])._request("GET", "/api/v3/ping")
    assert len(calls) == 1


async def test_negative_body_code_is_fatal():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -1121, "msg": "Invalid symbol."})

    with pytest.raises(FatalExchangeError):
        await _client(handler, [])._request("GET", "/api/v3/ping")
