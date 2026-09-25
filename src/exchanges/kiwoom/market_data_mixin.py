# ratchet-allow: REST endpoint paths / api-id values / response field names
# are a best-effort placeholder structure (see auth.py module docstring), not
# confirmed against a live Kiwoom account in this session.
"""BR-23(task-7569) — KiwoomAdapter market data methods (ticker/orderbook/
ohlcv), mapping raw REST responses into `src/exchanges/common/types.py`'s
shared models (`Ticker`/`OrderBook`/`Candle`, `src/data/models/market_data.py`).

Structural pattern follows `kis/market_data_mixin.py` — one REST call per
quote type, KeyError on missing expected fields raises `FatalExchangeError`
(fail-closed: a malformed/partial payload must not silently produce a
half-populated model), all prices/quantities parsed as `Decimal` (never
`float`, CLAUDE.md §3).

Endpoint paths/`api-id` values/response field names below carry the same
DOC_ONLY-pending-confirmation caveat as `auth.py` (see its module docstring)
— they are a structurally-plausible placeholder, not a cited Kiwoom
reference. `capabilities.py`'s `KIWOOM_KR_EQUITY_PROFILE.verified="DOC_ONLY"`
reflects this.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker

_MARKET_CODE = "J"  # KRX, same convention as kis/market_data_mixin.py
_KRX_CODE = re.compile(r"\d{6}")


class _KiwoomHTTPClient(Protocol):
    """Structural contract this mixin requires of `self` — satisfied by
    `KiwoomAuthClient._request` when the two are combined in
    `src/exchanges/kiwoom/factory.py::KiwoomAdapter` (same pattern as
    `common/http_client.py`'s `KISHTTPClient` Protocol, kept local here since
    this leaf's scope excludes the common file)."""

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def _validate_krx_symbol(symbol: str) -> str:
    """LA-19-equivalent fail-closed symbol check — a malformed symbol must
    not be silently forwarded to the exchange (same principle as
    `kis/market_data_mixin.py`'s `symbol_normalizer.to_canonical` call; this
    leaf uses a local check instead of `foundation/market_data`'s shared
    `Venue` enum since that file is out of this leaf's scope and has no
    Kiwoom member)."""
    if not _KRX_CODE.fullmatch(symbol):
        raise ValueError(f"Kiwoom 종목코드는 6자리 숫자여야 함: {symbol!r}")
    return symbol


class KiwoomMarketDataMixin:
    async def get_ticker(self: _KiwoomHTTPClient, symbol: str) -> Ticker:
        symbol = _validate_krx_symbol(symbol)
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/market-data/ticker",
            "KW_TICKER",
            params={"market": _MARKET_CODE, "symbol": symbol},
        )
        try:
            output = raw["output"]
            return Ticker(
                symbol=symbol,
                exchange="kiwoom",
                price=Decimal(output["current_price"]),
                bid=Decimal(output["bid_price"]),
                ask=Decimal(output["ask_price"]),
                volume_24h=Decimal(output["volume"]),
                timestamp=datetime.now(timezone.utc),  # no separate timestamp field in the response
                source_type="primary",
            )
        except (KeyError, TypeError) as exc:
            raise FatalExchangeError(f"Kiwoom ticker 응답에 예상 필드 없음: {exc}") from exc

    async def get_orderbook(self: _KiwoomHTTPClient, symbol: str, depth: int = 20) -> OrderBook:
        symbol = _validate_krx_symbol(symbol)
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/market-data/orderbook",
            "KW_ORDERBOOK",
            params={"market": _MARKET_CODE, "symbol": symbol},
        )
        try:
            output = raw["output"]
            bids = [
                OrderBookLevel(price=Decimal(level["price"]), quantity=Decimal(level["quantity"]))
                for level in output["bids"][:depth]
            ]
            asks = [
                OrderBookLevel(price=Decimal(level["price"]), quantity=Decimal(level["quantity"]))
                for level in output["asks"][:depth]
            ]
        except (KeyError, TypeError) as exc:
            raise FatalExchangeError(f"Kiwoom orderbook 응답에 예상 필드 없음: {exc}") from exc

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
                f"KiwoomAdapter는 현재 일봉(1d)만 지원 — '{timeframe}'. 분봉 등은 "
                "이 leaf의 스콥 밖(추후 leaf에서 라이브 확인 후 확장)."
            )
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/market-data/ohlcv",
            "KW_OHLCV_DAY",
            params={"market": _MARKET_CODE, "symbol": symbol, "period": "D"},
        )
        rows = raw.get("output", [])[:limit]
        candles = []
        try:
            for row in rows:
                day = row["date"]  # "YYYYMMDD"
                open_time = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
                candles.append(
                    Candle(
                        symbol=symbol,
                        exchange="kiwoom",
                        timeframe="1d",
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        open_time=open_time,
                        close_time=open_time,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise FatalExchangeError(f"Kiwoom 일봉 응답에 예상 필드 없음: {exc}") from exc
        return candles
