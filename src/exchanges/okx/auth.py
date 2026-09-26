"""BR-21b -- OKX authentication/signing + rate-limited HTTP client.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 1/6(b).

Signing scheme (verified 2026-09-26 against the official OKX v5 API
reference, www.okx.com/docs-v5/en, "REST Authentication" section):
- prehash = timestamp + method.upper() + requestPath(+ "?" + queryString) + body
- sign = base64(HMAC-SHA256(secret, prehash))
- `timestamp` is an ISO-8601 UTC string with millisecond precision and a
  trailing "Z" (e.g. "2020-12-08T09:08:57.715Z") -- NOT epoch seconds/ms
  the way Bitget's ACCESS-TIMESTAMP is. This is the single biggest
  deviation from `bitget/adapter.py`'s otherwise near-identical header
  shape.
- Headers: OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-TIMESTAMP /
  OK-ACCESS-PASSPHRASE + Content-Type: application/json. Demo trading
  adds `x-simulated-trading: 1` (OKX's own header name for what Bitget
  calls `paptrading`).

429 handling: this module does not reimplement retry/backoff -- it wires
the existing `ResilientTransport` (L4-11/L4-12, `src/exchanges/common/
transport.py`) the same way `bitget/adapter.py` does. `classify_http`
already maps HTTP 429 to `ExchangeErrorKind.RATE_LIMITED` (retryable), and
`backoff_delay` already computes full-jitter exponential backoff -- no new
retry logic is written here, only OKX's signing/header/body-classification
glue around that shared pipeline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.common.transport import ResilientTransport

BASE_URL = "https://www.okx.com"
_SUCCESS_CODE = "0"

# Same shape as bitget/adapter.py's _RETRY_POLICY: max_attempts=4 (1 initial
# + 3 retries), full-jitter exponential backoff up to a 16s ceiling. OKX's
# own documented rate limits are per-endpoint and tighter than Bitget's, so
# the cap is kept lower than Bitget's 30.0 to avoid a single stuck request
# blocking a caller for as long.
RETRY_POLICY = RetryPolicy(max_attempts=4, base=0.5, cap=16.0)


def sign_request(
    secret: str, timestamp: str, method: str, request_path: str, body: str = ""
) -> str:
    """Pure function -- no network/IO, no real key required to unit test.

    `request_path` must already include the query string (if any) exactly
    as sent on the wire -- the signature covers the full path, same
    convention as `bitget/adapter.py::_sign`.
    """
    prehash = timestamp + method.upper() + request_path + body
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def iso_timestamp_from_epoch_ms(epoch_ms: int) -> str:
    """Converts an epoch-ms integer to OKX's required ISO-8601-with-millis
    format. Kept separate from `sign_request` so a caller can reuse a
    `ServerClock`-corrected epoch value (same pattern as bitget/adapter.py's
    `self._transport.clock.now_ms()`) instead of the raw system clock."""
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def build_headers(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    *,
    timestamp: str,
    method: str,
    request_path: str,
    body: str = "",
    demo_mode: bool = True,
) -> dict[str, str]:
    """Pure function assembling the full OKX v5 signed-request header set."""
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign_request(api_secret, timestamp, method, request_path, body),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json",
    }
    if demo_mode:
        headers["x-simulated-trading"] = "1"
    return headers


class OKXHTTPClient:
    """REST request signing/transport shared by every OKX mixin -- mixins
    reach it via `self._request()` (same assembly convention as
    `bitget/adapter.py::_BitgetHTTPClient`)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        *,
        demo_mode: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._demo_mode = demo_mode
        self._client = http_client or httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)
        transport_kwargs: dict[str, Any] = {"venue": "okx", "retry_policy": RETRY_POLICY}
        if rng is not None:
            transport_kwargs["rng"] = rng
        self._transport = ResilientTransport(
            sleep=sleep_fn or asyncio.sleep,
            **transport_kwargs,
        )

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = iso_timestamp_from_epoch_ms(self._transport.clock.now_ms())
        return build_headers(
            self._api_key,
            self._api_secret,
            self._api_passphrase,
            timestamp=timestamp,
            method=method,
            request_path=request_path,
            body=body,
            demo_mode=self._demo_mode,
        )

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            # Same override reasoning as bitget/adapter.py::_classify_body --
            # a non-JSON 200 is treated as a transient issue worth one retry
            # round rather than an immediate fatal (this single evaluation
            # is not itself put on the retry loop).
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="okx",
                http_status=response.status_code,
                message=f"OKX response is not JSON: {response.text}",
            )
        code = data.get("code")
        if code == _SUCCESS_CODE:
            return None
        # OKX's own rate-limit body code (50011, "Too Many Requests") can
        # arrive with HTTP 200 in some endpoints per official docs -- treat
        # it as retryable the same way HTTP 429 already is, instead of only
        # relying on the HTTP-status path in ResilientTransport.
        retryable = code == "50011"
        return ExchangeError(
            ExchangeErrorKind.RATE_LIMITED if retryable else ExchangeErrorKind.UNKNOWN_RESPONSE,
            retryable=retryable,
            venue="okx",
            http_status=response.status_code,
            venue_code=code,
            message=f"OKX API error: {data}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_string = ""
        if params:
            # Same reasoning as bitget/adapter.py::_request -- reuse httpx's
            # own query-encoding for both the signature and the actual
            # request so they can never disagree on percent-encoding.
            query_string = "?" + str(httpx.QueryParams(params))
        body_str = json.dumps(body) if body else ""
        request_path = path + query_string

        async def send_once() -> httpx.Response:
            headers = self._headers(method, request_path, body_str)
            return await self._client.request(
                method, request_path, content=body_str or None, headers=headers
            )

        try:
            response = await self._transport.request(send_once, classify_body=self._classify_body)
        except ExchangeError as exc:
            if exc.retryable:
                raise RetryableExchangeError(str(exc)) from exc
            raise FatalExchangeError(str(exc)) from exc

        data: dict[str, Any] = response.json()
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
