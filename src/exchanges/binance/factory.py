"""BR-22b -- BinanceAdapter assembly + BR-9 factory extension-point registration.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 3.

Combines `BinanceHTTPClient` with the market-data/account/trading mixins and
calls `register_exchange_adapter_factory("binance", ...)` at import time;
`src/exchanges/factory.py`'s `if exchange == ...` branches are untouched.

Unsupported in this leaf (fail-closed via `_unsupported`, never an empty
success): `subscribe_ticker_stream` -- no Binance WS mixin is wired yet.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from src.data.models.base import AssetClass
from src.exchanges.binance.account_mixin import BinanceAccountMixin
from src.exchanges.binance.auth import BinanceHTTPClient
from src.exchanges.binance.market_data_mixin import BinanceMarketDataMixin
from src.exchanges.binance.trading_mixin import BinanceTradingMixin
from src.exchanges.binance.venue_profile import BINANCE_SPOT_PROFILE
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability
from src.exchanges.factory import register_exchange_adapter_factory

if TYPE_CHECKING:
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

EXCHANGE_NAME = "binance"


class BinanceAdapter(
    BinanceHTTPClient,
    BinanceMarketDataMixin,
    BinanceAccountMixin,
    BinanceTradingMixin,
    ExchangeAdapter,
):
    @property
    def is_paper_trading(self) -> bool:
        return self._demo_mode

    @property
    def is_sandboxed(self) -> bool:
        return self._demo_mode

    def get_capabilities(self) -> ExchangeCapability:
        return ExchangeCapability(
            exchange_name=EXCHANGE_NAME,
            supported_asset_classes=[AssetClass.CRYPTO],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=False,
            max_leverage=Decimal("1"),
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )

    def venue_profile(self) -> VenueCapabilityProfile:
        """Wires the `binance/venue_profile.py` constant -- the ABC default
        raises `UnsupportedCapabilityError`, so this override is required."""
        return BINANCE_SPOT_PROFILE

    async def subscribe_ticker_stream(self, symbol: str, callback: Any) -> None:  # noqa: ARG002
        raise self._unsupported("subscribe_ticker_stream")

    async def health_check(self) -> bool:
        """Unsigned `GET /api/v3/ping`; any failure collapses to False."""
        try:
            await self._request("GET", "/api/v3/ping")
            return True
        except Exception:  # noqa: BLE001 -- health check collapses any failure to False
            return False


def _binance_adapter_factory(
    api_key: str, api_secret: str, extra: dict[str, str], demo_mode: bool
) -> BinanceAdapter:
    """BR-9 registered factory signature -- Binance needs no extra field."""
    del extra
    return BinanceAdapter(api_key, api_secret, demo_mode=demo_mode)


register_exchange_adapter_factory(EXCHANGE_NAME, _binance_adapter_factory)
