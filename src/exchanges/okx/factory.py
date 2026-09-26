"""BR-21b -- OKXAdapter assembly + BR-9 factory extension-point registration.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 3.

This is OKX's `adapter.py` equivalent (Bitget/KIS/NH each have one) --
named `factory.py` per this leaf's file list because its second job is the
BR-9 registration call itself. Combines `OKXHTTPClient` (auth.py) with
every mixin into one concrete `ExchangeAdapter` subclass, then calls
`register_exchange_adapter_factory("okx", ...)` at import time -- the same
top-level-call pattern this doc's step 3 example shows. Importing this
module (e.g. from application bootstrap) is what actually activates the
"okx" exchange name in `build_adapter()`; nothing here edits
`src/exchanges/factory.py`'s `if exchange == ...` branches (verified by
`git diff` staying empty on that file, and by
`tests/exchanges/okx/test_okx_factory.py`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.models.base import AssetClass
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability
from src.exchanges.factory import UnsupportedExchangeError, register_exchange_adapter_factory
from src.exchanges.okx.account_mixin import OKXAccountMixin
from src.exchanges.okx.auth import OKXHTTPClient
from src.exchanges.okx.market_data_mixin import OKXMarketDataMixin
from src.exchanges.okx.trading_mixin import OKXTradingMixin
from src.exchanges.okx.venue_profile import OKX_SPOT_PROFILE
from src.exchanges.okx.websocket import OKXWebSocketMixin

if TYPE_CHECKING:
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

EXCHANGE_NAME = "okx"


class OKXAdapter(
    OKXHTTPClient,
    OKXMarketDataMixin,
    OKXAccountMixin,
    OKXTradingMixin,
    OKXWebSocketMixin,
    ExchangeAdapter,
):
    @property
    def is_paper_trading(self) -> bool:
        return self._demo_mode

    @property
    def is_sandboxed(self) -> bool:
        """Red-team audit (2026-09-01-08) -- exposes the constructor's
        `demo_mode` verbatim, same value/reasoning as
        `bitget/adapter.py::is_sandboxed`."""
        return self._demo_mode

    def get_capabilities(self) -> ExchangeCapability:
        """Phase 1 scope is spot-only (per `okx/trading_mixin.py`'s module
        docstring) -- futures/margin are not declared here even though OKX
        itself supports them, same "declaring is a green light" principle
        `bitget/adapter.py::get_capabilities` documents."""
        return ExchangeCapability(
            exchange_name=EXCHANGE_NAME,
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
        """Wires the `okx/venue_profile.py` constant -- the ABC's default
        implementation raises `UnsupportedCapabilityError`, so this override
        is required (ADDING_AN_EXCHANGE.md step 2's KIS/NH defect warning:
        defining the constant alone is not enough)."""
        return OKX_SPOT_PROFILE

    async def health_check(self) -> bool:
        """Watchdog-facing lightweight liveness probe -- same
        try/get_balance/except-swallow-to-False pattern as
        `bitget/trading_plan_mixin.py::health_check`."""
        try:
            await self.get_balance()
            return True
        except Exception:  # noqa: BLE001 -- health check collapses any failure to False
            return False


def _okx_adapter_factory(
    api_key: str, api_secret: str, extra: dict[str, str], demo_mode: bool
) -> OKXAdapter:
    """BR-9 registered factory signature -- `extra` is unused because OKX
    needs exactly one extra credential field (`api_passphrase`), same shape
    as Bitget's factory branch in `src/exchanges/factory.py::build_adapter`."""
    try:
        api_passphrase = extra["api_passphrase"]
    except KeyError as exc:
        raise UnsupportedExchangeError("OKX requires an api_passphrase credential field.") from exc
    return OKXAdapter(api_key, api_secret, api_passphrase, demo_mode=demo_mode)


register_exchange_adapter_factory(EXCHANGE_NAME, _okx_adapter_factory)
