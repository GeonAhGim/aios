"""BR-22b -- BinanceMarketDataMixin: get_ticker/get_orderbook/get_ohlcv.

Endpoints (DOC_ONLY -- Binance Spot API docs `rest-api.md`, public/unsigned,
not exercised live in this session):
- GET /api/v3/ticker/24hr?symbol= -- `lastPrice`, `bidPrice`, `askPrice`,
  `volume` (24h base volume), `closeTime` (ms epoch).
- GET /api/v3/depth?symbol=&limit= -- `bids`/`asks` as `[price, qty]`
  string pairs. The response carries no timestamp, so the observation time
  (UTC now) is used.
- GET /api/v3/klines?symbol=&interval=&limit= -- rows
  `[openTime, o, h, l, c, volume, closeTime, ...]`, already oldest-first.
  Binance interval vocabulary ("1m", "1h", "1d") matches AIOS lowercase.

Symbols are canonical ("BTC/USDT") and converted via `to_binance_symbol`;
an unknown/ill-formed symbol raises instead of being guessed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.binance.symbols import to_binance_symbol


class _BinanceMarketDataClient(Protocol):
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any: ...


def _utc_from_ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


class BinanceMarketDataMixin:
    async def get_ticker(self: _BinanceMarketDataClient, symbol: str) -> Ticker:
        raw = await self._request(
            "GET", "/api/v3/ticker/24hr", params={"symbol": to_binance_symbol(symbol)}
        )
        try:
            last = Decimal(str(raw["lastPrice"]))
            return Ticker(
                symbol=symbol,
                exchange="binance",
                price=last,
                bid=Decimal(str(raw.get("bidPrice") or raw["lastPrice"])),
                ask=Decimal(str(raw.get("askPrice") or raw["lastPrice"])),
                volume_24h=Decimal(str(raw.get("volume", "0"))),
                timestamp=_utc_from_ms(raw["closeTime"]),
                source_type="primary",
            )
        except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"Binance ticker response missing/invalid field: {exc}, raw={raw!r}"
            ) from exc

    async def get_orderbook(
        self: _BinanceMarketDataClient, symbol: str, depth: int = 20
    ) -> OrderBook:
        raw = await self._request(
            "GET",
            "/api/v3/depth",
            params={"symbol": to_binance_symbol(symbol), "limit": depth},
        )
        try:
            bids = [
                OrderBookLevel(price=Decimal(str(lv[0])), quantity=Decimal(str(lv[1])))
                for lv in raw["bids"]
            ]
            asks = [
                OrderBookLevel(price=Decimal(str(lv[0])), quantity=Decimal(str(lv[1])))
                for lv in raw["asks"]
            ]
        except (KeyError, IndexError, TypeError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"Binance depth response missing/invalid field: {exc}, raw={raw!r}"
            ) from exc
        return OrderBook(
            symbol=symbol,
            exchange="binance",
            bids=bids[:depth],
            asks=asks[:depth],
            timestamp=datetime.now(UTC),
        )

    async def get_ohlcv(
        self: _BinanceMarketDataClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        rows = await self._request(
            "GET",
            "/api/v3/klines",
            params={"symbol": to_binance_symbol(symbol), "interval": timeframe, "limit": limit},
        )
        if not isinstance(rows, list):
            raise FatalExchangeError(f"Binance klines response is not an array: {rows!r}")
        try:
            return [
                Candle(
                    symbol=symbol,
                    exchange="binance",
                    timeframe=timeframe,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])),
                    open_time=_utc_from_ms(row[0]),
                    close_time=_utc_from_ms(row[6]),
                )
                for row in rows
            ]
        except (IndexError, TypeError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"Binance klines row malformed (expected [ot,o,h,l,c,v,ct,...]): {exc}"
            ) from exc
