# ratchet-allow: base URL / endpoint path / rate-limit values are a best-effort
# placeholder structure (see module docstring below), not confirmed against a
# live Kiwoom account in this session.
"""BR-23(task-7569) — Kiwoom Securities REST API OAuth2 token client + shared
signing/transport logic (exchange onboarding step (b)).

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md, ADR-2026-09-06-I("브로커 우선, 100%
커버리지"), docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21.

Structural pattern follows `kis/oauth_client.py` (KIS is the closest sibling —
Korean broker, REST API, OAuth2 client-credentials, per-account-type rate
limiting), per task instruction to mirror the existing NH/KIS adapters.

Honesty caveat (§10 principle) — unlike `kis/oauth_client.py`, which cites the
KIS official GitHub sample source as its verified basis, the base URLs,
endpoint path, and response field names below were **not** confirmed against
a live Kiwoom REST API account or fetched documentation in this session. They
are a structurally-plausible placeholder (same request/response shape as
KIS/NH: OAuth2 client-credentials, Bearer token, per-call TR-like id header).
`capabilities.py`'s `verified="DOC_ONLY"` on the venue profile reflects this
caveat — treat as "needs live/doc reconciliation", not "confirmed". A
follow-up leaf must reconcile these against Kiwoom's actual published REST
API reference before any live capital path is wired through this venue.

Retry/backoff/circuit-breaking/clock-skew handling is delegated to
`ResilientTransport` (L4-12, `common/transport.py`), same as KIS/NH — not
reimplemented here. Token caching reuses `MonotonicTokenCache` (BR-10,
`common/oauth_http.py`). Rate-limit profile (account type -> `TokenBucket`
config) lives in `rate_profile.py` — split out to stay under the
architecture guard's 300-line adapter-file cap, same reason
`kis/rate_profile.py` is split from `kis/oauth_client.py`.

Scope note — this leaf covers only auth + market data (step (b)); account
(task-7570), trading (task-7571), and websocket (task-7572) are sibling
leaves and are not touched. `KiwoomAuthClient` is therefore a self-contained
client (not a mixin needing further composition with a separate
`adapter.py`, unlike `kis/oauth_client.py`'s `_KISTokenTransportMixin`) —
`src/exchanges/kiwoom/factory.py` combines it with `KiwoomMarketDataMixin`
directly.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.oauth_http import MonotonicTokenCache
from src.exchanges.common.transport import RateLimitWaitObserver, ResilientTransport
from src.exchanges.kiwoom.rate_profile import KiwoomAccountType, build_token_bucket

# Placeholder — structurally mirrors KIS's real/paper base URL split, not a
# confirmed Kiwoom URL (see module docstring).
REAL_BASE_URL = "https://api.kiwoom.com"
PAPER_BASE_URL = "https://mockapi.kiwoom.com"


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
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )

        try:
            response = await self._transport.request(send_once)
        except ExchangeError as exc:
            raise FatalExchangeError(f"Kiwoom 토큰 발급 실패: {exc}") from exc

        try:
            data = response.json()
            token = data["token"]
            if not isinstance(token, str) or not token:
                raise KeyError("token")
            expires_in = float(data.get("expires_dt_seconds", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise FatalExchangeError(f"Kiwoom 토큰 응답 형식 오류: {exc}") from exc

        # 60s safety margin — same conservative discipline as NH's
        # "expires_in - 60s" policy (`common/oauth_http.py` module docstring),
        # since Kiwoom's actual expiry semantics are unconfirmed here.
        self._token_cache.set(token, max(expires_in - 60.0, 0.0))
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
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "api-id": api_id,
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
                message=f"Kiwoom 응답이 JSON이 아님: {response.text}",
            )
        if not isinstance(data, dict):
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=False,
                venue="kiwoom",
                http_status=response.status_code,
                message=f"Kiwoom 응답이 객체가 아님: {data!r}",
            )
        if data.get("return_code") == 0:
            return None
        return ExchangeError(
            ExchangeErrorKind.UNKNOWN_RESPONSE,
            retryable=True,
            venue="kiwoom",
            http_status=response.status_code,
            message=f"Kiwoom API 오류: {data}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A 401 (AUTH) invalidates the token and retries the original
        request exactly once — a retry that also gets a 401 is surfaced
        as-is. HTTP status/network retry and backoff are handled by
        `ResilientTransport` and are not reimplemented here."""
        transport = await self._transport_for_requests()
        retried_after_auth = False
        while True:
            headers = await self._headers(api_id)

            async def send_once(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._client.request(
                    method, path, params=params, json=body, headers=headers
                )

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
