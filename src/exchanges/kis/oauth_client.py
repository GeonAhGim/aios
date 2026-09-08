"""6.9/L4-21 — KIS OAuth2 token issuance/caching + shared signing/transport logic.

Spec: 02_exchange_adapter_v1.2.md#§2.1,
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21

Auth/endpoints (confirmed 2026-08-28 against the KIS official GitHub sample
source at github.com/koreainvestment/open-trading-api):
- OAuth2: POST /oauth2/tokenP, body {grant_type:"client_credentials",
  appkey, appsecret} → {access_token, access_token_token_expired} (valid for 1 day)
- Base URL: real trading https://openapi.koreainvestment.com:9443,
  paper trading https://openapivts.koreainvestment.com:29443
- Request headers: Content-Type/Accept/charset + authorization: Bearer {token} +
  appkey + appsecret + tr_id + custtype: "P"
- tr_id real/paper substitution: if the leading character is T/J/C, paper
  trading substitutes it with 'V' (e.g. TTTC8434R → VTTC8434R). Market-data
  lookups (tr_id starting with F) use the same tr_id for both real and paper.
- Response format: {rt_cd: "0"(success)|other, msg_cd, msg1, output/output1/output2}

`adapter.py` (300-line cap, exceeded by new L4-21 logic) had its token/transport
logic split out into this file (a pure move, no behavior change — the same
call made for the bitget/trading_query_mixin.py split). Only `_resolve_tr_id`/
`_PAPER_SWAP_PREFIXES` stay in `adapter.py` — because
`tests/unit/exchanges/kis/test_overseas_futureoption_tr_reference
.py::test_paper_swap_rule_mutation_breaks_tr_id_identity` directly patches that
module's global via
`monkeypatch.setattr(adapter_module, "_PAPER_SWAP_PREFIXES", ...)` — since a
global reference is bound to the module it's defined in (not a closure, but
the namespace of the module where the function is defined), moving
`_resolve_tr_id` here would silently break that monkeypatch.

Retry/backoff/circuit-breaking/clock-skew handling is not reimplemented here;
it's delegated to `ResilientTransport` (L4-12, common/transport.py). The token
cache reuses the `MonotonicTokenCache` that NH (BR-10, oauth_http.py) landed
first (this leaf is the follow-up leaf for what was noted as "the KIS
migration is a follow-up leaf's job") — wrapped in an `asyncio.Lock` so that
even when multiple coroutines arrive concurrently while the token is expired,
the token issuance endpoint is called exactly once (double-checked locking,
DoD a).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.oauth_http import MonotonicTokenCache
from src.exchanges.common.transport import ResilientTransport

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"


class _KISTokenTransportMixin:
    """Shared OAuth2 token issuance/caching + request transport logic.
    `adapter.py`'s `_KISHTTPClient(_KISTokenTransportMixin)` completes this by
    adding `_resolve_tr_id` — since `self._resolve_tr_id(tr_id)`, called from
    `_headers()`, is an instance attribute lookup, it works correctly
    regardless of which file it lives in (see the module docstring)."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        cano: str,
        acnt_prdt_cd: str,
        *,
        is_paper_trading: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._cano = cano
        self._acnt_prdt_cd = acnt_prdt_cd
        self._is_paper_trading = is_paper_trading
        base_url = PAPER_BASE_URL if is_paper_trading else REAL_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._transport = ResilientTransport(venue="kis", sleep=sleep_fn or asyncio.sleep)
        self._token_cache = MonotonicTokenCache()
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        cached = self._token_cache.get()
        if cached is not None:
            return cached
        async with self._token_lock:
            # double-checked — while waiting on the lock, another coroutine may
            # have already issued the token.
            cached = self._token_cache.get()
            if cached is not None:
                return cached
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        async def send_once() -> httpx.Response:
            return await self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                },
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )

        try:
            response = await self._transport.request(send_once)
        except ExchangeError as exc:
            raise FatalExchangeError(f"KIS 토큰 발급 실패: {exc}") from exc

        data = response.json()
        token: str = data["access_token"]
        # KIS returns the expiry time as a "YYYY-MM-DD HH:MM:SS" string (valid
        # for 1 day), but here we conservatively cache for only 23 hours to
        # avoid reusing the token right before it expires.
        self._token_cache.set(token, 23 * 3600)
        return token

    def _invalidate_token(self) -> None:
        # ttl=0 → the next get() always treats it as expired
        # (MonotonicTokenCache.get()'s `<` comparison doesn't pass even for
        # the same instant — deterministic, no race).
        self._token_cache.set("", 0.0)

    def _resolve_tr_id(self, tr_id: str) -> str:
        """Real/paper tr_id substitution. `adapter.py`'s `_KISHTTPClient`
        supplies the actual implementation (see the module docstring) — this
        stub is never called directly."""
        raise NotImplementedError

    async def _headers(self, tr_id: str) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "text/plain",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": self._resolve_tr_id(tr_id),
            "custtype": "P",
        }

    def _classify_body(self, response: httpx.Response) -> ExchangeError | None:
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=True,
                venue="kis",
                http_status=response.status_code,
                message=f"KIS 응답이 JSON이 아님: {response.text}",
            )
        if data.get("rt_cd") == "0":
            return None
        return ExchangeError(
            ExchangeErrorKind.UNKNOWN_RESPONSE,
            retryable=True,
            venue="kis",
            http_status=response.status_code,
            message=f"KIS API 오류: {data}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """DoD(b) — a 401 (AUTH) invalidates the token and retries the
        original request exactly once. If the retry also gets a 401, it does
        not retry again and surfaces the exception as-is. HTTP status
        code/network retry and backoff are handled by `ResilientTransport`
        (DoD c) and are not reimplemented here."""
        retried_after_auth = False
        while True:
            headers = await self._headers(tr_id)

            async def send_once(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._client.request(
                    method, path, params=params, json=body, headers=headers
                )

            try:
                response = await self._transport.request(
                    send_once, classify_body=self._classify_body
                )
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
