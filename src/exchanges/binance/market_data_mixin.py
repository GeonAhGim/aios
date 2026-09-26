"""BR-22b -- BinanceMarketDataMixin: get_ticker/get_orderbook/get_ohlcv.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 1 (Binance).

Endpoints (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`
section "Market Data endpoints") -- all three have Endpoint security type
`NONE`: no `X-MBX-APIKEY` header or signature is sent for these paths
(`BINANCE_PUBLIC_PATHS` below is what `factory.py`'s `_request` checks to
skip signing).
- GET /api/v3/ticker/24hr?symbol=... -- 24hr rolling-window ticker.
  Response fields used: `lastPrice`, `bidPrice`, `askPrice`, `volume`.
- GET /api/v3/depth?symbol=...&limit=... -- order book snapshot. Response:
  `bids`/`asks`, each a list of `[price, quantity]` string pairs.
- GET /api/v3/klines?symbol=...&interval=...&limit=... -- candlesticks.
  Response is a bare JSON array (not wrapped in an object, unlike the two
  endpoints above): each row is
  `[openTime, open, high, low, close, volume, closeTime, ...]`.

Deviation: because `get_ohlcv`'s response is a top-level array while the
other two are objects, this file's local `_request` contract declares
`-> Any` (not `dict[str, Any]`) -- `trading_mixin.py`'s `_BinanceOrderClient`
Protocol (a different, stricter local contract for order endpoints, which
never receive an array body) is unaffected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker

_TICKER_PATH = "/api/v3/ticker/24hr"
_DEPTH_PATH = "/api/v3/depth"
_KLINES_PATH = "/api/v3/klines"

# Endpoint security type NONE (module docstring) -- `factory.py`'s `_request`
# skips API-KEY/signature for exactly these paths.
BINANCE_PUBLIC_PATHS = frozenset({_TICKER_PATH, _DEPTH_PATH, _KLINES_PATH})

# AIOS standard timeframe -> Binance `interval` kline parameter. Binance also
# accepts 1s/3m/2h/... variants this adapter does not expose (AIOS's own
# timeframe vocabulary, ADR-2026-08-28 multi-asset expansion, is the
# authority here, not Binance's full interval list).
_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


class _BinanceMarketDataClient(Protocol):
    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any: ...


class BinanceMarketDataMixin:
    async def get_ticker(self: _BinanceMarketDataClient, symbol: str) -> Ticker:
        raw = await self._request("GET", _TICKER_PATH, params={"symbol": symbol})
        if not isinstance(raw, dict):
            raise FatalExchangeError(f"Binance ticker response is not an object: {raw!r}")
        try:
            return Ticker(
                symbol=symbol,
                exchange="binance",
                price=Decimal(str(raw["lastPrice"])),
                bid=Decimal(str(raw["bidPrice"])),
                ask=Decimal(str(raw["askPrice"])),
                volume_24h=Decimal(str(raw["volume"])),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )
        except KeyError as exc:
            raise FatalExchangeError(
                f"Binance ticker response missing expected field: {exc}"
            ) from exc

    async def get_orderbook(
        self: _BinanceMarketDataClient, symbol: str, depth: int = 20
    ) -> OrderBook:
        raw = await self._request(
            "GET", _DEPTH_PATH, params={"symbol": symbol, "limit": str(depth)}
        )
        if not isinstance(raw, dict):
            raise FatalExchangeError(f"Binance orderbook response is not an object: {raw!r}")
        try:
            bids = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw["bids"]]
            asks = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw["asks"]]
        except (KeyError, ValueError, TypeError) as exc:
            raise FatalExchangeError(f"Binance orderbook response malformed: {exc}") from exc
        return OrderBook(
            symbol=symbol,
            exchange="binance",
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(
        self: _BinanceMarketDataClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        interval = _INTERVAL_MAP.get(timeframe)
        if interval is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        raw = await self._request(
            "GET",
            _KLINES_PATH,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        if not isinstance(raw, list):
            raise FatalExchangeError(f"Binance klines response is not a list: {raw!r}")
        candles: list[Candle] = []
        try:
            for row in raw:
                candles.append(
                    Candle(
                        symbol=symbol,
                        exchange="binance",
                        timeframe=timeframe,
                        open=Decimal(str(row[1])),
                        high=Decimal(str(row[2])),
                        low=Decimal(str(row[3])),
                        close=Decimal(str(row[4])),
                        volume=Decimal(str(row[5])),
                        open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                        close_time=datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc),
                    )
                )
        except (IndexError, TypeError, ValueError) as exc:
            raise FatalExchangeError(f"Binance klines row malformed: {exc}") from exc
        return candles
