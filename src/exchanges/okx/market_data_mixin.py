"""BR-21b -- OKXMarketDataMixin: get_ticker/get_orderbook/get_ohlcv.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 1/6(b).

Endpoints (verified 2026-09-26 against the official OKX v5 API reference,
www.okx.com/docs-v5/en, "Market Data" section -- public, no signature
required, but this mixin still goes through `self._request()` so the same
transport/retry pipeline covers public and private calls alike):
- GET /api/v5/market/ticker?instId=<symbol> -- `data[0]`: `last` (last
  traded price), `askPx`/`bidPx` (best ask/bid), `vol24h` (24h base-ccy
  volume), `ts` (ms epoch string).
- GET /api/v5/market/books?instId=<symbol>&sz=<depth> -- `data[0]`:
  `asks`/`bids`, each row `[price, size, deprecated_liquidated_orders,
  numOrders]` (only the first two fields are used here -- the third is
  documented as always "0", the fourth is order count, neither is part of
  the `OrderBookLevel` contract).
- GET /api/v5/market/candles?instId=<symbol>&bar=<timeframe>&limit=<n> --
  `data`: array of `[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`
  rows, **newest first** (reversed here to the oldest-first order
  `Candle` list consumers expect, matching `Candle.open_time <
  Candle.close_time` ordering used elsewhere in this codebase).

`bar` values are OKX's own timeframe vocabulary (e.g. "1m", "1H", "1D" --
note the uppercase hour/day suffix, unlike Bitget's lowercase). This mixin
does not translate the standard AIOS lowercase "1h"/"1d" convention into
OKX's mixed-case one (unlike Bitget's `_WS_CANDLE_CHANNEL_MAP`) -- that
translation table is deferred to the leaf that actually wires
`get_ohlcv()` timeframe input from a caller (out of this leaf's scope,
same "documented gap over a guessed table" posture as the ratchet notes
elsewhere in this package).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker


class _OKXMarketDataClient(Protocol):
    """Minimal HTTP contract this mixin needs of `self` -- same reasoning as
    `okx/trading_mixin.py::_OKXOrderClient` (declared locally since a shared
    `OKXHTTPClient` type import would create an import cycle at
    mixin-assembly time before `factory.py` combines everything)."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def _first_data_row(raw: dict[str, Any], *, path: str) -> dict[str, Any]:
    data = raw.get("data")
    if not data:
        raise FatalExchangeError(f"OKX {path} response has an empty data array: {raw!r}")
    row: Any = data[0]
    if not isinstance(row, dict):
        raise FatalExchangeError(f"OKX {path} response data[0] is not an object: {raw!r}")
    return row


class OKXMarketDataMixin:
    async def get_ticker(self: _OKXMarketDataClient, symbol: str) -> Ticker:
        raw = await self._request("GET", "/api/v5/market/ticker", params={"instId": symbol})
        row = _first_data_row(raw, path="/api/v5/market/ticker")
        try:
            last = Decimal(str(row["last"]))
            bid_raw = row.get("bidPx") or row["last"]
            ask_raw = row.get("askPx") or row["last"]
            return Ticker(
                symbol=symbol,
                exchange="okx",
                price=last,
                bid=Decimal(str(bid_raw)),
                ask=Decimal(str(ask_raw)),
                volume_24h=Decimal(str(row.get("vol24h", "0"))),
                timestamp=datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc),
                source_type="primary",
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX ticker response missing/invalid field (last/ts required): {exc}, row={row!r}"
            ) from exc

    async def get_orderbook(
        self: _OKXMarketDataClient, symbol: str, depth: int = 20
    ) -> OrderBook:
        raw = await self._request(
            "GET", "/api/v5/market/books", params={"instId": symbol, "sz": str(depth)}
        )
        row = _first_data_row(raw, path="/api/v5/market/books")
        try:
            bids = [
                OrderBookLevel(price=Decimal(str(level[0])), quantity=Decimal(str(level[1])))
                for level in row["bids"]
            ]
            asks = [
                OrderBookLevel(price=Decimal(str(level[0])), quantity=Decimal(str(level[1])))
                for level in row["asks"]
            ]
            timestamp = datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc)
        except (KeyError, IndexError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX orderbook response missing/invalid field (bids/asks/ts required): "
                f"{exc}, row={row!r}"
            ) from exc
        return OrderBook(
            symbol=symbol, exchange="okx", bids=bids[:depth], asks=asks[:depth], timestamp=timestamp
        )

    async def get_ohlcv(
        self: _OKXMarketDataClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        raw = await self._request(
            "GET",
            "/api/v5/market/candles",
            params={"instId": symbol, "bar": timeframe, "limit": str(limit)},
        )
        rows = raw.get("data")
        if rows is None:
            raise FatalExchangeError(f"OKX candles response missing data array: {raw!r}")
        candles: list[Candle] = []
        try:
            for row in rows:
                open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
                candles.append(
                    Candle(
                        symbol=symbol,
                        exchange="okx",
                        timeframe=timeframe,
                        open=Decimal(str(row[1])),
                        high=Decimal(str(row[2])),
                        low=Decimal(str(row[3])),
                        close=Decimal(str(row[4])),
                        volume=Decimal(str(row[5])),
                        open_time=open_time,
                        close_time=open_time,
                    )
                )
        except (IndexError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX candles response row malformed (expected [ts,o,h,l,c,vol,...]): "
                f"{exc}, rows={rows!r}"
            ) from exc
        # OKX returns newest-first; AIOS callers expect oldest-first (same
        # ordering as NH/Bitget get_ohlcv results).
        candles.reverse()
        return candles
