"""BR-23(task-7569) — KiwoomAdapter assembly (auth/market-data/account/
trading/websocket mixins) and the BR-9 factory function, per
`docs/exchanges/ADDING_AN_EXCHANGE.md` §1/§3.

Spec: ADR-2026-09-06-I Decision 5, docs/specs/L4_execution_oms_and_exchange_v1.0.md
§2-B (factory.py row), §9 L4-13.

`KiwoomAdapter` assembles every Kiwoom leaf now on main: auth (this leaf,
`auth.py`) + market data (this leaf, `market_data_mixin.py`) + account
(task-7570, `account_mixin.py`) + trading (task-7571, `trading_mixin.py`) +
websocket (task-7572, `websocket.py`). `place_order`/`cancel_order`/
`modify_order` carry `@require_paper_sandbox` (`common/live_guard.py`,
red-team #2026-09-02-32) inside `trading_mixin.py` itself, not here.

Registration itself happens in `src/exchanges/factory.py` (the "registration
only" file in this leaf's scope) via `register_exchange_adapter_factory` —
this module does not call it, to avoid importing back into
`src.exchanges.factory` (that file already imports `kiwoom_factory` from
here) and creating a cycle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.live_guard import require_paper_sandbox
from src.exchanges.common.types import ExchangeCapability, TickerCallback
from src.exchanges.kiwoom.account_mixin import KiwoomAccountMixin
from src.exchanges.kiwoom.auth import KiwoomAuthClient
from src.exchanges.kiwoom.capabilities import KIWOOM_CAPABILITY, KIWOOM_KR_EQUITY_PROFILE
from src.exchanges.kiwoom.market_data_mixin import KiwoomMarketDataMixin
from src.exchanges.kiwoom.trading_mixin import KiwoomTradingMixin
from src.exchanges.kiwoom.websocket import KiwoomWebSocketMixin
from src.exchanges.kiwoom.websocket_connection import ConnectFn, ReconnectHook, connect

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

    from src.data.models.market_data import Candle, OrderBook, Ticker
    from src.data.models.trading import AccountBalance, Order, Position
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile


class KiwoomAdapter(
    KiwoomWebSocketMixin,
    KiwoomTradingMixin,
    KiwoomAccountMixin,
    KiwoomMarketDataMixin,
    KiwoomAuthClient,
    ExchangeAdapter,
):
    """Full BR-23 adapter assembly. Every `ExchangeAdapter`-abstract method
    below is explicitly redeclared in this class's own body (a thin
    passthrough into the mixin that actually implements it) rather than left
    to plain MRO inheritance -- `scripts/consistency/wiring.py`'s
    `port_method_unimplemented` check reads only a class's own AST body, not
    its MRO, so an ABC method only provided by a mixin would otherwise read
    as "missing" (the same architectural gap bitget/kis/nh's own market-data
    mixins already have, baselined in `consistency-baseline.json`)."""

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
        KiwoomAuthClient.__init__(
            self,
            app_key,
            app_secret,
            account_no,
            is_paper_trading=is_paper_trading,
            http_client=http_client,
            sleep_fn=sleep_fn,
        )

    @property
    def is_paper_trading(self) -> bool:
        return self._is_paper_trading

    @property
    def is_sandboxed(self) -> bool:
        """Red-team audit #2026-09-01-08 — independent of `is_paper_trading`
        in principle, but this adapter has no separate sandbox-binding signal
        of its own (same as every other concrete adapter today), so it
        mirrors the constructor flag, same as bitget/kis/nh."""
        return self._is_paper_trading

    def get_capabilities(self) -> ExchangeCapability:
        return KIWOOM_CAPABILITY

    def venue_profile(self) -> VenueCapabilityProfile:
        return KIWOOM_KR_EQUITY_PROFILE

    async def health_check(self) -> bool:
        """Uses token issuance as the liveness probe (a lighter call than a
        real balance query, same "light call" spirit as the ABC docstring
        asks for)."""
        try:
            await self._ensure_token()
        except Exception:  # noqa: BLE001 — any transport/auth failure means "not healthy"
            return False
        return True

    async def get_ticker(self, symbol: str) -> Ticker:
        return await KiwoomMarketDataMixin.get_ticker(self, symbol)

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        return await KiwoomMarketDataMixin.get_orderbook(self, symbol, depth)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        return await KiwoomMarketDataMixin.get_ohlcv(self, symbol, timeframe, limit)

    async def subscribe_ticker_stream(
        self,
        symbol: str,
        callback: TickerCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = connect,
    ) -> None:
        # `cast` (not `# type: ignore`) works around a mypy Protocol-matching
        # quirk: `KiwoomWsAuthClient` (websocket_connection.py, sibling leaf)
        # declares `is_paper_trading` as a plain (implicitly settable)
        # attribute, but this class only ever exposes it as a read-only
        # `@property` (as ABCMeta requires to clear the abstract property) --
        # the mixin only ever reads it, never assigns it, so this is safe.
        await KiwoomWebSocketMixin.subscribe_ticker_stream(
            cast(Any, self),
            symbol,
            callback,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
            connect_fn=connect_fn,
        )

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        return await KiwoomAccountMixin.get_balance(self, asset)

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        return await KiwoomAccountMixin.get_positions(self, symbol)

    async def get_order(self, order_id: str) -> Order:
        return await KiwoomAccountMixin.get_order(self, order_id)

    # `@require_paper_sandbox` here is a deliberate second guard on top of
    # `trading_mixin.py`'s own decorator on the methods below -- the repo's
    # AST-based `tests/unit/exchanges/test_live_guard_coverage.py` scans
    # every class's own fund-moving methods for the decorator directly (same
    # "own AST body, not MRO" limitation as the consistency checker above),
    # so this class needs its own copy regardless of the mixin already
    # carrying one (harmless: `is_paper_trading and is_sandboxed` is checked
    # twice, not a behavior change).
    @require_paper_sandbox
    async def place_order(self, order: Order) -> Order:
        return await KiwoomTradingMixin.place_order(self, order)

    @require_paper_sandbox
    async def cancel_order(self, order_id: str) -> bool:
        return await KiwoomTradingMixin.cancel_order(self, order_id)

    @require_paper_sandbox
    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        return await KiwoomTradingMixin.modify_order(self, order_id, **kwargs)


class KiwoomFactoryConfigError(ValueError):
    """`extra` is missing a field `kiwoom_factory` requires."""


def kiwoom_factory(
    api_key: str, api_secret: str, extra: dict[str, str], demo_mode: bool
) -> ExchangeAdapter:
    """`RegisteredAdapterFactory` signature (BR-9,
    `src/exchanges/factory.py`'s `register_exchange_adapter_factory`).
    Credentials are read only from the `extra` mapping the caller supplies
    (the existing config/credential-resolution pattern) — no env var or file
    read happens here."""
    try:
        account_no = extra["account_no"]
    except KeyError as exc:
        raise KiwoomFactoryConfigError("Kiwoom requires account_no in extra") from exc
    return KiwoomAdapter(api_key, api_secret, account_no, is_paper_trading=demo_mode)
