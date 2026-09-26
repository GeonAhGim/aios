"""BR-22b -- BinanceAdapter assembly + BR-9 factory registration.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md steps 1-3 (Binance).

This is Binance's composition root (the role `bitget/adapter.py` plays for
Bitget) -- it owns the signed-HTTP client every mixin's local `_request`
Protocol expects (`trading_mixin.py`'s `_BinanceOrderClient`,
`market_data_mixin.py`/`account_mixin.py`/`websocket.py`'s equivalents),
assembles `BinanceAdapter` from all of them, and registers it via BR-9's
`register_exchange_adapter_factory` extension point (task-1787,
ADR-2026-09-06-I D5) -- `src/exchanges/factory.py`'s `if exchange == ...`
branches are not touched (this is exactly what that extension point is
for; see `tests/unit/exchanges/test_factory_spi_extension.py`).

Endpoint security types (verified 2026-09-26 against the official docs,
`rest-api.md` §"Endpoint security type"):
- NONE (`market_data_mixin.BINANCE_PUBLIC_PATHS` + `/api/v3/ping`): no
  header, no signature.
- USER_STREAM (`websocket.USER_DATA_STREAM_PATH`): `X-MBX-APIKEY` header
  only, no signature.
- SIGNED (everything else -- account_mixin.py/trading_mixin.py paths):
  `X-MBX-APIKEY` header + `auth.build_signed_query`.

429/418 backoff: `_request` retries a rate-limited response
(`auth.is_retryable_status` -- covers 429/418/5xx) using
`auth.next_backoff_delay`, deterministic here via `rng=lambda: 1.0` (same
reasoning as `bitget/adapter.py`'s `_BitgetHTTPClient` -- full-jitter with
rng pinned to 1.0 reproduces plain exponential backoff, which is what the
retry tests assert against).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.exchanges.binance.account_mixin import BinanceAccountMixin
from src.exchanges.binance.auth import build_signed_query, is_retryable_status, next_backoff_delay
from src.exchanges.binance.market_data_mixin import BINANCE_PUBLIC_PATHS, BinanceMarketDataMixin
from src.exchanges.binance.trading_mixin import BinanceTradingMixin
from src.exchanges.binance.venue_profile import BINANCE_SPOT_PROFILE
from src.exchanges.binance.websocket import USER_DATA_STREAM_PATH, BinanceWebSocketMixin
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.clock_sync import ServerClock
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.common.types import ExchangeCapability
from src.exchanges.factory import register_exchange_adapter_factory

if TYPE_CHECKING:
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

LIVE_BASE_URL = "https://api.binance.com"
TESTNET_BASE_URL = "https://testnet.binance.vision"
_PING_PATH = "/api/v3/ping"
_SERVER_TIME_PATH = "/api/v3/time"
_UNSIGNED_PATHS = BINANCE_PUBLIC_PATHS | {_PING_PATH}

_RETRY_POLICY = RetryPolicy(max_attempts=4, base=1.0, cap=30.0)

logger = logging.getLogger(__name__)


class _BinanceHTTPClient:
    """Signed REST request assembly + 429/418 backoff. Mixins reach this via
    `self._request()`."""

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
        base_url = TESTNET_BASE_URL if demo_mode else LIVE_BASE_URL
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._clock = ServerClock()
        self._sleep = sleep_fn or asyncio.sleep
        self._rng = rng or (lambda: 1.0)

    async def _fetch_server_time_ms(self) -> int:
        response = await self._client.get(_SERVER_TIME_PATH)
        return int(response.json()["serverTime"])

    async def sync_server_time(self) -> None:
        """Best-effort -- a sync failure must not block signing (same
        fail-open posture as `bitget/adapter.py::sync_server_time`;
        FROZEN-PAPER-ONLY guards the trading path independently)."""
        try:
            await self._clock.sync(self._fetch_server_time_ms)
        except Exception as exc:  # noqa: BLE001 -- see docstring, swallowing is the contract
            logger.debug("Binance server-time sync failed (non-fatal): %s", exc)

    def _build_query(self, path: str, params: dict[str, str]) -> tuple[str, dict[str, str]]:
        if path in _UNSIGNED_PATHS:
            return urlencode(params), {}
        headers = {"X-MBX-APIKEY": self._api_key}
        if path == USER_DATA_STREAM_PATH:
            return urlencode(params), headers
        timestamp_ms = self._clock.now_ms()
        query = build_signed_query(params, secret=self._api_secret, timestamp_ms=timestamp_ms)
        return query, headers

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        string_params = {k: str(v) for k, v in (params or {}).items()}
        query, headers = self._build_query(path, string_params)
        return await self._send_with_retry(method, path, query, headers)

    async def _send_with_retry(
        self, method: str, path: str, query: str, headers: dict[str, str]
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            url = f"{path}?{query}" if query else path
            response = await self._client.request(method, url, headers=headers)
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise FatalExchangeError(
                        f"Binance response is not JSON (status={response.status_code}): "
                        f"{response.text}"
                    ) from exc
            if is_retryable_status(response.status_code) and attempt < _RETRY_POLICY.max_attempts:
                delay = next_backoff_delay(
                    attempt=attempt,
                    retry_after_header=response.headers.get("Retry-After"),
                    policy=_RETRY_POLICY,
                    rng=self._rng,
                )
                await self._sleep(delay)
                continue
            if is_retryable_status(response.status_code):
                raise RetryableExchangeError(
                    f"Binance rate-limited (status={response.status_code}), retries exhausted: "
                    f"{response.text}"
                )
            raise FatalExchangeError(
                f"Binance HTTP error (status={response.status_code}): {response.text}"
            )

    async def aclose(self) -> None:
        await self._client.aclose()


class BinanceAdapter(
    _BinanceHTTPClient,
    BinanceMarketDataMixin,
    BinanceAccountMixin,
    BinanceTradingMixin,
    BinanceWebSocketMixin,
    ExchangeAdapter,
):
    @property
    def is_paper_trading(self) -> bool:
        return self._demo_mode

    @property
    def is_sandboxed(self) -> bool:
        """Same single-flag posture as BitgetAdapter (redteam 2026-09-01-08)
        -- exposes the constructor's `demo_mode` verbatim, no separate
        state."""
        return self._demo_mode

    def get_capabilities(self) -> ExchangeCapability:
        """Phase 1 scope is spot-only (same posture as BitgetAdapter) --
        Binance also offers futures/margin, not declared here."""
        return ExchangeCapability(
            exchange_name="binance",
            supported_asset_classes=[AssetClass.CRYPTO],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=True,
            max_leverage=Decimal("1"),
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )

    def venue_profile(self) -> VenueCapabilityProfile:
        """Exposes `venue_profile.py`'s constant -- the ABC default would
        otherwise raise `UnsupportedCapabilityError` (ADDING_AN_EXCHANGE.md
        §2's "constant defined but never wired" pitfall)."""
        return BINANCE_SPOT_PROFILE

    async def health_check(self) -> bool:
        """Lightweight, unauthenticated connectivity probe (module
        docstring's NONE security type) -- no balance call needed."""
        try:
            await self._request("GET", _PING_PATH)
        except (FatalExchangeError, RetryableExchangeError):
            return False
        return True


def _binance_adapter_factory(
    api_key: str, api_secret: str, _extra: dict[str, str], demo_mode: bool
) -> ExchangeAdapter:
    """BR-9 factory signature (`RegisteredAdapterFactory` in
    `src/exchanges/factory.py`) -- Binance needs no `extra` fields (no
    passphrase/account number, unlike Bitget/KIS/NH)."""
    return BinanceAdapter(api_key, api_secret, demo_mode=demo_mode)


register_exchange_adapter_factory("binance", _binance_adapter_factory)
