"""BR-23(task-7569) — KiwoomAdapter market data methods (ticker/orderbook/
ohlcv), mapping raw REST responses into `src/exchanges/common/types.py`'s
shared models (`Ticker`/`OrderBook`/`Candle`, `src/data/models/market_data.py`).

Verified 2026-09-26 (WebFetch, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`examples/domestic_stock/market_condition/get_domestic_stock_market_condition_info.py`
(ka10007) + `examples/domestic_stock/chart/get_domestic_stock_daily_chart.py`
(ka10081), official Kiwoom Securities REST API client repo — same source
cited by `auth.py`/`account_mixin.py`/`trading_mixin.py`):
- Ticker + orderbook (a single Kiwoom TR covers both): POST
  /api/dostk/mrkcond, api-id ka10007, body {stk_cd}. Top-level response
  fields (no wrapper key): `cur_prc` (current price), `trde_qty` (volume),
  10-level book as `sel_1bid`..`sel_10bid`/`sel_1bid_req`..`sel_10bid_req`
  (ask price/qty) and `buy_1bid`..`buy_10bid`/`buy_1bid_req`..`buy_10bid_req`
  (bid price/qty).
- OHLCV (daily): POST /api/dostk/chart, api-id ka10081, body {stk_cd,
  base_dt, upd_stkpc_tp}. Rows are a list under the `stk_dt_pole_chart_qry`
  key: `dt` (date), `open_pric`/`high_pric`/`low_pric`/`cur_prc` (OHLC —
  Kiwoom reuses `cur_prc` for "close" in this TR, not a typo), `trde_qty`
  (volume). Multi-page pagination (`cont-yn`/`next-key` response headers,
  confirmed in `kiwoom/core/client.py`) is out of this leaf's scope — only
  the first page `_request` returns is read, same as this leaf's `limit`
  truncation.

All prices/quantities parsed as `Decimal` (never `float`, CLAUDE.md §3).
Kiwoom's legacy field convention sometimes prefixes a numeric string with a
sign character (its own display-formatting code special-cases a leading
"-"); `Decimal` already parses a leading "+"/"-" and surrounding whitespace
natively, so values are only `.strip()`-ed defensively before conversion.
KeyError on missing expected fields raises `FatalExchangeError` (fail-closed
— a malformed/partial payload must not silently produce a half-populated
model), mirroring `kis/market_data_mixin.py`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker

_MARKET_COND_PATH = "/api/dostk/mrkcond"
_API_ID_MARKET_COND = "ka10007"
_CHART_PATH = "/api/dostk/chart"
_API_ID_DAILY_CHART = "ka10081"
_CHART_ROWS_KEY = "stk_dt_pole_chart_qry"

_KRX_CODE = re.compile(r"\d{6}")


class _KiwoomHTTPClient(Protocol):
    """Structural contract this mixin requires of `self` — satisfied by
    `KiwoomAuthClient._request` when combined in
    `src/exchanges/kiwoom/factory.py::KiwoomAdapter` (same pattern as
    `common/http_client.py`'s `KISHTTPClient` Protocol, kept local here since
    this leaf's scope excludes the common file)."""

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def _validate_krx_symbol(symbol: str) -> str:
    """Fail-closed symbol check — a malformed symbol must not be silently
    forwarded to the exchange (same principle as `kis/market_data_mixin.py`'s
    `symbol_normalizer.to_canonical` call; this leaf uses a local check
    instead of `foundation/market_data`'s shared `Venue` enum since that file
    is out of this leaf's scope and has no Kiwoom member)."""
    if not _KRX_CODE.fullmatch(symbol):
        raise ValueError(f"Kiwoom stock code must be 6 digits: {symbol!r}")
    return symbol


def _decimal(raw: Any) -> Decimal:
    return Decimal(str(raw).strip())


class KiwoomMarketDataMixin:
    async def get_ticker(self: _KiwoomHTTPClient, symbol: str) -> Ticker:
        symbol = _validate_krx_symbol(symbol)
        raw = await self._request(
            "POST", _MARKET_COND_PATH, _API_ID_MARKET_COND, body={"stk_cd": symbol}
        )
        try:
            return Ticker(
                symbol=symbol,
                exchange="kiwoom",
                price=_decimal(raw["cur_prc"]),
                bid=_decimal(raw["buy_1bid"]),
                ask=_decimal(raw["sel_1bid"]),
                volume_24h=_decimal(raw["trde_qty"]),
                timestamp=datetime.now(timezone.utc),  # no separate timestamp field in the response
                source_type="primary",
            )
        except (KeyError, TypeError) as exc:
            raise FatalExchangeError(
                f"Kiwoom ticker response missing expected field: {exc}"
            ) from exc

    async def get_orderbook(self: _KiwoomHTTPClient, symbol: str, depth: int = 20) -> OrderBook:
        symbol = _validate_krx_symbol(symbol)
        raw = await self._request(
            "POST", _MARKET_COND_PATH, _API_ID_MARKET_COND, body={"stk_cd": symbol}
        )
        try:
            bids = [
                OrderBookLevel(
                    price=_decimal(raw[f"buy_{i}bid"]), quantity=_decimal(raw[f"buy_{i}bid_req"])
                )
                for i in range(1, min(depth, 10) + 1)
                if f"buy_{i}bid" in raw
            ]
            asks = [
                OrderBookLevel(
                    price=_decimal(raw[f"sel_{i}bid"]), quantity=_decimal(raw[f"sel_{i}bid_req"])
                )
                for i in range(1, min(depth, 10) + 1)
                if f"sel_{i}bid" in raw
            ]
        except (KeyError, TypeError) as exc:
            raise FatalExchangeError(
                f"Kiwoom orderbook response missing expected field: {exc}"
            ) from exc

        return OrderBook(
            symbol=symbol,
            exchange="kiwoom",
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(
        self: _KiwoomHTTPClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        symbol = _validate_krx_symbol(symbol)
        if timeframe != "1d":
            raise ValueError(
                f"KiwoomAdapter currently only supports daily (1d) candles — got {timeframe!r}. "
                "Intraday timeframes are out of this leaf's scope."
            )
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._request(
            "POST",
            _CHART_PATH,
            _API_ID_DAILY_CHART,
            body={"stk_cd": symbol, "base_dt": today, "upd_stkpc_tp": "1"},
        )
        rows = raw.get(_CHART_ROWS_KEY, [])[:limit]
        candles = []
        try:
            for row in rows:
                day = row["dt"]  # "YYYYMMDD"
                open_time = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
                candles.append(
                    Candle(
                        symbol=symbol,
                        exchange="kiwoom",
                        timeframe="1d",
                        open=_decimal(row["open_pric"]),
                        high=_decimal(row["high_pric"]),
                        low=_decimal(row["low_pric"]),
                        close=_decimal(row["cur_prc"]),  # Kiwoom reuses cur_prc as "close" here
                        volume=_decimal(row["trde_qty"]),
                        open_time=open_time,
                        close_time=open_time,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise FatalExchangeError(
                f"Kiwoom daily chart row missing expected field: {exc}"
            ) from exc
        return candles
