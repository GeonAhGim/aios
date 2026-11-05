"""BR-22b -- Binance authentication/signing + rate-limited HTTP client.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 1/6(b).

Signing scheme (DOC_ONLY -- read from the public Binance Spot API docs,
github.com/binance/binance-spot-api-docs, `rest-api.md` "SIGNED Endpoint
security"; not exercised against a live key in this session):
- signature = hex(HMAC-SHA256(secret, totalParams)) where totalParams is the
  query string exactly as sent on the wire.
- `timestamp` (ms epoch) and optional `recvWindow` are ordinary query
  params and are part of the signed payload; `signature` is appended last.
- API key travels in the `X-MBX-APIKEY` header.

Rate limiting: HTTP 429 goes through the shared `ResilientTransport`
(Retry-After aware full-jitter backoff). HTTP 418 means the IP was
auto-banned for continuing after 429s; retrying inside the ban window only
extends it, so 418 is surfaced immediately as a `RetryableExchangeError`
(caller-level backoff) instead of being hammered by the transport loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.common.transport import ResilientTransport

BASE_URL = "https://api.binance.com"
TESTNET_BASE_URL = "https://testnet.binance.vision"
DEFAULT_RECV_WINDOW_MS = 5000
IP_BANNED_STATUS = 418

RETRY_POLICY = RetryPolicy(max_attempts=4, base=0.5, cap=16.0)

# Unsigned market-data / liveness endpoints; everything else is SIGNED.
PUBLIC_PATHS = frozenset({"/api/v3/ping", "/api/v3/ticker/24hr", "/api/v3/depth", "/api/v3/klines"})


def sign_query(secret: str, query_string: str) -> str:
    """Pure function -- no network/IO, no real key required to unit test."""
    mac = hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def build_signed_query(
    secret: str,
    params: dict[str, Any] | None,
    *,
    timestamp_ms: int,
    recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
) -> str:
    """Pure function: params + recvWindow + timestamp -> full query string
    ending in `&signature=<hex>`."""
    merged = dict(params or {})
    merged["recvWindow"] = recv_window_ms
    merged["timestamp"] = timestamp_ms
    query = urlencode(merged)
    return f"{query}&signature={sign_query(secret, query)}"


class BinanceHTTPClient:
    """REST signing/transport shared by every Binance mixin -- mixins reach
    it via `self._request()`."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        demo_mode: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._demo_mode = demo_mode
        base_url = TESTNET_BASE_URL if demo_mode else BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        transport_kwargs: dict[str, Any] = {"venue": "binance", "retry_policy": RETRY_POLICY}
        if rng is not None:
            transport_kwargs["rng"] = rng
        self._transport = ResilientTransport(sleep=sleep_fn or asyncio.sleep, **transport_kwargs)

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data = response.json()
        except ValueError:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="binance",
                http_status=response.status_code,
                message=f"Binance response is not JSON: {response.text}",
            )
        # Errors are {"code": <negative int>, "msg": "..."}; successful
        # payloads are objects without "code" or bare arrays (klines).
        if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=False,
                venue="binance",
                http_status=response.status_code,
                venue_code=str(data["code"]),
                message=f"Binance API error: {data}",
            )
        return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        signed = path not in PUBLIC_PATHS

        async def send_once() -> httpx.Response:
            # Re-evaluated per attempt so the signed timestamp is fresh.
            if signed:
                query = build_signed_query(
                    self._api_secret,
                    params,
                    timestamp_ms=self._transport.clock.now_ms(),
                )
            else:
                query = urlencode(params or {})
            url = f"{path}?{query}" if query else path
            return await self._client.request(method, url, headers={"X-MBX-APIKEY": self._api_key})

        try:
            response = await self._transport.request(send_once, classify_body=self._classify_body)
        except ExchangeError as exc:
            if exc.retryable or exc.http_status == IP_BANNED_STATUS:
                raise RetryableExchangeError(str(exc)) from exc
            raise FatalExchangeError(str(exc)) from exc
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
