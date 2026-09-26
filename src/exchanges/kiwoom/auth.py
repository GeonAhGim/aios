"""BR-23(task-7569) — Kiwoom Securities REST API OAuth2 token client + shared
signing/transport logic (exchange onboarding step (b)).

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md, ADR-2026-09-06-I ("broker first,
100% coverage"), docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21.

Structural pattern follows `kis/oauth_client.py` (KIS is the closest sibling —
Korean broker, REST API, OAuth2 client-credentials, per-account-type rate
limiting).

Verified 2026-09-26 (WebFetch, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`kiwoom/core/auth.py` + `kiwoom/core/client.py` + `kiwoom/core/errors.py`,
official Kiwoom Securities REST API client repo -- same source cited by
`account_mixin.py`/`trading_mixin.py`/`websocket*.py`, task-7570/7571/7572):
- OAuth2: POST /oauth2/token, body {grant_type: "client_credentials", appkey,
  secretkey} -> {token, token_type, expires_dt ("%Y%m%d%H%M%S", KST),
  return_code, return_msg}.
- Base URL: real https://api.kiwoom.com, sandbox https://mockapi.kiwoom.com.
- Authenticated (TR) request headers: Content-Type, api-id, authorization
  ("Bearer {token}") only -- appkey/secretkey are not resent as headers once
  a token has been issued.
- Kiwoom returns business errors at HTTP 200 with a non-zero `return_code`
  (fail-closed default: unmapped codes are `UNKNOWN_RESPONSE`,
  `retryable=False`). Two code sets matter here: an expired/invalid token
  can surface as top-level `return_code` in {8005, 8031, 8103} *or* embedded
  in `return_msg` as `"[8005:...]"`/`"CODE=8005"` -- both trigger the same
  invalidate-and-retry-once path as an HTTP 401 would (`_auth_retry_code`,
  mirroring `errors.py`'s `auth_retry_code`/`embedded_return_code`).
  `{1700, 1701, 1702}` are the server-side rate-limit codes.

Retry/backoff/circuit-breaking/clock-skew handling is delegated to
`ResilientTransport` (L4-12, `common/transport.py`), same as KIS/NH — not
reimplemented here. Token caching reuses `MonotonicTokenCache` (BR-10,
`common/oauth_http.py`). Rate-limit profile (account type -> `TokenBucket`
config) lives in `rate_profile.py` — split out to stay under the
architecture guard's 300-line adapter-file cap, same reason
`kis/rate_profile.py` is split from `kis/oauth_client.py`. The exact
per-second call-rate limit itself is not published anywhere in the source
above, so `rate_profile.py` still uses a conservative estimate
(`verified="ESTIMATED"`) rather than a cited number.

Scope note — this leaf covers only auth + market data (step (b)); account
(task-7570), trading (task-7571), and websocket (task-7572) are sibling
leaves. `KiwoomAuthClient` is a self-contained client (not a mixin needing
further composition with a separate `adapter.py`) -- `factory.py` combines
it with the market-data/account/trading/websocket mixins directly. Its
`_request(method, path, api_id, *, body=)` signature matches the structural
`_KiwoomOrderClient`/`_KiwoomAccountClient` Protocols those sibling mixins
already declare (no `params` -- every Kiwoom TR call observed in the
verified source sends its arguments in the JSON body, never as a query
string).
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.oauth_http import MonotonicTokenCache
from src.exchanges.common.transport import RateLimitWaitObserver, ResilientTransport
from src.exchanges.kiwoom.rate_profile import KiwoomAccountType, build_token_bucket

REAL_BASE_URL = "https://api.kiwoom.com"
PAPER_BASE_URL = "https://mockapi.kiwoom.com"

_KST = ZoneInfo("Asia/Seoul")
_EXPIRES_DT_FORMAT = "%Y%m%d%H%M%S"

# Kiwoom's own client (kiwoom/core/errors.py) treats these top-level (or
# return_msg-embedded) codes as "token expired/invalid -- refresh and retry".
_AUTH_RETRY_RETURN_CODES = frozenset({8005, 8031, 8103})
_RATE_LIMIT_RETURN_CODES = frozenset({1700, 1701, 1702})
_EMBEDDED_CODE_RE = re.compile(r"\[(\d{3,5}):|CODE=(\d{3,5})")


def _embedded_return_code(return_msg: object) -> int | None:
    match = _EMBEDDED_CODE_RE.search(str(return_msg or ""))
    if match is None:
        return None
    group = match.group(1) or match.group(2)
    return int(group) if group else None


def _auth_retry_code(return_code: object, return_msg: object) -> int | None:
    """The auth-expiry code (top-level or embedded) that warrants a token
    refresh + retry, else None (mirrors `kiwoom/core/errors.py`'s
    `auth_retry_code`)."""
    if isinstance(return_code, int) and return_code in _AUTH_RETRY_RETURN_CODES:
        return return_code
    embedded = _embedded_return_code(return_msg)
    return embedded if embedded in _AUTH_RETRY_RETURN_CODES else None


class KiwoomAuthClient:
    """OAuth2 token issue/cache/refresh + signed request transport.

    All HTTP goes through an injectable `httpx.AsyncClient` (`http_client`)
    and an injectable `sleep_fn` — tests supply an `httpx.MockTransport` and a
    fast fake sleep (same pattern as `tests/unit/exchanges/test_kis_auth.py`),
    so no real network or wall-clock wait is needed to exercise retry/backoff/
    rate-limit paths deterministically.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        *,
        is_paper_trading: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._is_paper_trading = is_paper_trading
        base_url = PAPER_BASE_URL if is_paper_trading else REAL_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._transport = ResilientTransport(venue="kiwoom", sleep=self._sleep_fn)
        self._token_cache = MonotonicTokenCache()
        self._token_lock = asyncio.Lock()
        self._rate_limit_observer = RateLimitWaitObserver()
        self._request_transport: ResilientTransport | None = None
        self._request_transport_lock = asyncio.Lock()

    def _account_type(self) -> KiwoomAccountType:
        return KiwoomAccountType.PAPER if self._is_paper_trading else KiwoomAccountType.REAL

    async def _ensure_token(self) -> str:
        cached = self._token_cache.get()
        if cached is not None:
            return cached
        async with self._token_lock:
            # double-checked — another coroutine may have already issued the
            # token while this one waited on the lock.
            cached = self._token_cache.get()
            if cached is not None:
                return cached
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        async def send_once() -> httpx.Response:
            return await self._client.post(
                "/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "secretkey": self._app_secret,
                },
                headers={"Content-Type": "application/json;charset=UTF-8"},
            )

        try:
            response = await self._transport.request(send_once)
        except ExchangeError as exc:
            raise FatalExchangeError(f"Kiwoom token issuance failed: {exc}") from exc

        try:
            data = response.json()
            token = data["token"]
            if not isinstance(token, str) or not token:
                raise KeyError("token")
            expires_at = datetime.strptime(data["expires_dt"], _EXPIRES_DT_FORMAT).replace(
                tzinfo=_KST
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise FatalExchangeError(f"Kiwoom token response malformed: {exc}") from exc

        # 60s safety margin so a token is never used right up to its expiry.
        ttl = (expires_at - datetime.now(_KST)).total_seconds()
        self._token_cache.set(token, max(ttl - 60.0, 0.0))
        return token

    def _invalidate_token(self) -> None:
        # ttl=0 → the next get() always treats it as expired.
        self._token_cache.set("", 0.0)

    async def _transport_for_requests(self) -> ResilientTransport:
        if self._request_transport is not None:
            return self._request_transport
        async with self._request_transport_lock:
            if self._request_transport is None:
                bucket = build_token_bucket(
                    self._account_type(),
                    observer=self._rate_limit_observer,
                    sleep=self._sleep_fn,
                )
                self._request_transport = ResilientTransport(
                    venue="kiwoom", rate_limiter=bucket, sleep=self._sleep_fn
                )
            return self._request_transport

    @property
    def rate_limit_wait_count(self) -> int:
        """Observability — number of times this client instance absorbed a
        call-rate excess as a wait instead of surfacing a rate-limit error."""
        return self._rate_limit_observer.waits

    async def _headers(self, api_id: str) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "authorization": f"Bearer {token}",
        }

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data = response.json()
        except ValueError:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="kiwoom",
                http_status=response.status_code,
                message=f"Kiwoom response is not JSON: {response.text}",
            )
        if not isinstance(data, dict):
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=False,
                venue="kiwoom",
                http_status=response.status_code,
                message=f"Kiwoom response is not an object: {data!r}",
            )
        return_code = data.get("return_code")
        if return_code == 0:
            return None
        auth_code = _auth_retry_code(return_code, data.get("return_msg"))
        if auth_code is not None:
            return ExchangeError(
                ExchangeErrorKind.AUTH,
                venue="kiwoom",
                venue_code=str(auth_code),
                http_status=response.status_code,
                message=f"Kiwoom auth-expiry code {auth_code}: {data}",
            )
        if return_code in _RATE_LIMIT_RETURN_CODES:
            return ExchangeError(
                ExchangeErrorKind.RATE_LIMITED,
                venue="kiwoom",
                venue_code=str(return_code),
                http_status=response.status_code,
                message=f"Kiwoom rate-limit code {return_code}: {data}",
            )
        return ExchangeError(
            ExchangeErrorKind.UNKNOWN_RESPONSE,
            venue="kiwoom",
            venue_code=str(return_code),
            http_status=response.status_code,
            message=f"Kiwoom API error: {data}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """An auth-expiry signal (HTTP 401, or the body-level codes
        `_auth_retry_code` recognizes) invalidates the token and retries the
        original request exactly once — a retry that also gets one is
        surfaced as-is. HTTP status/network retry and backoff are handled by
        `ResilientTransport` and are not reimplemented here."""
        transport = await self._transport_for_requests()
        retried_after_auth = False
        while True:
            headers = await self._headers(api_id)

            async def send_once(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._client.request(method, path, json=body, headers=headers)

            try:
                response = await transport.request(send_once, classify_body=self._classify_body)
            except ExchangeError as exc:
                if exc.kind is ExchangeErrorKind.AUTH and not retried_after_auth:
                    self._invalidate_token()
                    retried_after_auth = True
                    continue
                if exc.retryable:
                    raise RetryableExchangeError(str(exc)) from exc
                raise FatalExchangeError(str(exc)) from exc
            return dict(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
