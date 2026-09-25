"""BR-23(task-7569) — KiwoomAdapter assembly (auth + market data mixins) and
the BR-9 factory function, per `docs/exchanges/ADDING_AN_EXCHANGE.md` §1/§3.

Spec: ADR-2026-09-06-I Decision 5, docs/specs/L4_execution_oms_and_exchange_v1.0.md
§2-B(factory.py 행), §9 L4-13.

Scope — this leaf covers only auth + market data (step (b)). Account
(task-7570), trading (task-7571), and websocket (task-7572) are sibling
leaves and are not touched. `KiwoomAdapter` is nonetheless a concrete
`ExchangeAdapter` (all 14 abstract methods implemented, so ABCMeta allows
instantiation) — the methods those sibling leaves own raise
`self._unsupported(...)`, the ABC's own explicit "not supported" signal
(never a silent `[]`/`None`, per `common/adapter.py`'s module docstring) —
this is the "leave those SPI methods raising `self._unsupported(...)`"
option the task explicitly allows instead of leaving adapter assembly out of
scope entirely.

Registration itself happens in `src/exchanges/factory.py` (the "registration
only" file in this leaf's scope) via `register_exchange_adapter_factory` —
this module does not call it, to avoid importing back into
`src.exchanges.factory` (that file already imports `kiwoom_factory` from
here) and creating a cycle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.common.types import ExchangeCapability, TickerCallback
from src.exchanges.kiwoom.auth import KiwoomAuthClient
from src.exchanges.kiwoom.capabilities import KIWOOM_CAPABILITY, KIWOOM_KR_EQUITY_PROFILE
from src.exchanges.kiwoom.market_data_mixin import KiwoomMarketDataMixin

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

    from src.data.models.market_data import Candle, OrderBook, Ticker
    from src.data.models.trading import AccountBalance, Order, Position
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile


class KiwoomAdapter(KiwoomMarketDataMixin, KiwoomAuthClient, ExchangeAdapter):
    """Foundation-only adapter (BR-23 step (b)). `get_ticker`/`get_orderbook`/
    `get_ohlcv` come from `KiwoomMarketDataMixin`; auth/transport come from
    `KiwoomAuthClient`. Account/trading/websocket SPI methods raise
    `_unsupported()` until their sibling leaves land."""

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
        """레드팀 감사(2026-09-01-08) — independent of `is_paper_trading` in
        principle, but this adapter has no separate sandbox-binding signal of
        its own (same as every other concrete adapter today), so it mirrors
        the constructor flag, same as bitget/kis/nh."""
        return self._is_paper_trading

    def get_capabilities(self) -> ExchangeCapability:
        return KIWOOM_CAPABILITY

    def venue_profile(self) -> VenueCapabilityProfile:
        return KIWOOM_KR_EQUITY_PROFILE

    # Explicit pass-throughs (not just relying on MRO inheritance from
    # KiwoomMarketDataMixin) -- scripts/consistency/wiring.py's
    # `port_method_unimplemented` check reads each ExchangeAdapter subclass's
    # own AST body and does not resolve mixin MRO, so an ABC method only
    # provided by a mixin reads as "missing" (the same architectural gap is
    # already present, and already baselined, for bitget/kis/nh's own
    # market-data mixins). Redeclaring here keeps this leaf from adding new
    # hits without editing `consistency-baseline.json`.
    async def get_ticker(self, symbol: str) -> Ticker:
        return await KiwoomMarketDataMixin.get_ticker(self, symbol)

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        return await KiwoomMarketDataMixin.get_orderbook(self, symbol, depth)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        return await KiwoomMarketDataMixin.get_ohlcv(self, symbol, timeframe, limit)

    async def health_check(self) -> bool:
        """Uses token issuance as the liveness probe (no account/balance
        endpoint is in scope for this leaf yet)."""
        try:
            await self._ensure_token()
        except Exception:  # noqa: BLE001 — any transport/auth failure means "not healthy"
            return False
        return True

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:  # noqa: ARG002
        raise self._unsupported("subscribe_ticker_stream")

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:  # noqa: ARG002
        raise self._unsupported("get_balance")

    async def get_positions(self, symbol: str | None = None) -> list[Position]:  # noqa: ARG002
        raise self._unsupported("get_positions")

    async def get_order(self, order_id: str) -> Order:  # noqa: ARG002
        raise self._unsupported("get_order")

    async def place_order(self, order: Order) -> Order:  # noqa: ARG002
        raise self._unsupported("place_order")

    async def cancel_order(self, order_id: str) -> bool:  # noqa: ARG002
        raise self._unsupported("cancel_order")

    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:  # noqa: ARG002
        raise self._unsupported("modify_order")


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
        raise KiwoomFactoryConfigError("Kiwoom은 account_no가 필요합니다.") from exc
    return KiwoomAdapter(api_key, api_secret, account_no, is_paper_trading=demo_mode)
