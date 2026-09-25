"""02b_bitget_api_v2_full_spec_v1.md §5.1 — BitgetAdapter Futures Market(공개 시세) 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §5.1(P0), §9(작업 분해 3번)

`ExchangeAdapter` ABC에는 아직 없는 Bitget 전용 확장 메서드다(margin_mixin.py
모듈 docstring과 동일 원칙). `productType` 기본값은 `USDT-FUTURES` —
다른 상품(COIN-FUTURES/USDC-FUTURES)은 필요해지면 파라미터로 전달.

엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET /api/v2/mix/market/{contracts,ticker,merge-depth,candles,current-fund-rate}

2026-09-25 task-6797(P6.line_cap) — remaining 4 GET methods moved to
`futures_market_extra_mixin.py` (pure move, no behavior change).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.market_data import (
    Candle,
    FundingRate,
    OrderBook,
    OrderBookLevel,
    Ticker,
)
from src.data.models.trading import FuturesContractInfo
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.bitget.symbols import to_canonical_symbol as _to_canonical_symbol
from src.exchanges.common.http_client import SignedRequestClient

DEFAULT_PRODUCT_TYPE = "USDT-FUTURES"

# market_data_mixin.py의 _GRANULARITY_MAP과 값 동기화 필요(순환 임포트 방지 위해 복제).
_GRANULARITY_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}


def _rows_to_candles(rows: list[list[str]], symbol: str, timeframe: str) -> list[Candle]:
    """candles/history-candles 2개 엔드포인트가 공유하는 행 형태
    ([ts, open, high, low, close, baseVolume, ...])."""
    candles = []
    for row in rows:
        ts_ms, o, h, low, c, base_vol = row[0], row[1], row[2], row[3], row[4], row[5]
        open_time = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        candles.append(
            Candle(
                symbol=symbol,
                exchange="bitget",
                timeframe=timeframe,
                open=Decimal(o),
                high=Decimal(h),
                low=Decimal(low),
                close=Decimal(c),
                volume=Decimal(base_vol),
                open_time=open_time,
                close_time=open_time,
            )
        )
    return candles


class BitgetFuturesMarketMixin:
    async def get_futures_contracts(
        self: SignedRequestClient, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[FuturesContractInfo]:
        raw = await self._request(
            "GET", "/api/v2/mix/market/contracts", params={"productType": product_type}
        )
        return [
            FuturesContractInfo(
                symbol=item["symbol"],
                exchange="bitget",
                base_coin=item.get("baseCoin", ""),
                quote_coin=item.get("quoteCoin", ""),
                min_order_size=Decimal(item.get("minTradeNum", "0")),
                price_tick_size=Decimal(item.get("priceEndStep", "0")),
                size_tick_size=Decimal(item.get("volumePlace", "0")),
                max_leverage=Decimal(item.get("maxLever", "1")),
            )
            for item in raw["data"]
        ]

    async def get_futures_ticker(
        self: SignedRequestClient, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Ticker:
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/ticker",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return Ticker(
            symbol=symbol,
            exchange="bitget",
            price=Decimal(data["lastPr"]),
            bid=Decimal(data.get("bidPr", data["lastPr"])),
            ask=Decimal(data.get("askPr", data["lastPr"])),
            volume_24h=Decimal(data.get("baseVolume", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    async def get_futures_tickers(
        self: SignedRequestClient, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[Ticker]:
        """02b 스펙 §5.1(P0) — 전체 심볼 현재가. get_futures_ticker()의
        단일 심볼 조회와 짝을 이루는 전체 조회(문서상 별도 엔드포인트)."""
        raw = await self._request(
            "GET", "/api/v2/mix/market/tickers", params={"productType": product_type}
        )
        return [
            Ticker(
                symbol=_to_canonical_symbol(item["symbol"]),
                exchange="bitget",
                price=Decimal(item["lastPr"]),
                bid=Decimal(item.get("bidPr", item["lastPr"])),
                ask=Decimal(item.get("askPr", item["lastPr"])),
                volume_24h=Decimal(item.get("baseVolume", "0")),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )
            for item in raw["data"]
        ]

    async def get_futures_orderbook(
        self: SignedRequestClient,
        symbol: str,
        *,
        depth: int = 20,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> OrderBook:
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/merge-depth",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "limit": str(depth),
            },
        )
        data = raw["data"]
        return OrderBook(
            symbol=symbol,
            exchange="bitget",
            bids=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in data["bids"]],
            asks=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in data["asks"]],
            timestamp=datetime.now(timezone.utc),
        )

    async def get_futures_candles(
        self: SignedRequestClient,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[Candle]:
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "granularity": granularity,
                "limit": str(limit),
            },
        )
        return _rows_to_candles(raw["data"], symbol, timeframe)

    async def get_futures_history_candles(
        self: SignedRequestClient,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
        end_time: str | None = None,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[Candle]:
        """02b 스펙 §5.1(P0) — get_futures_candles()의 과거 캔들 버전(FD-2.3)."""
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        params: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
            "granularity": granularity,
            "limit": str(limit),
        }
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request("GET", "/api/v2/mix/market/history-candles", params=params)
        return _rows_to_candles(raw["data"], symbol, timeframe)

    async def get_futures_history_funding_rate(
        self: SignedRequestClient,
        symbol: str,
        *,
        limit: int = 100,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[FundingRate]:
        """02b 스펙 §5.1(P1)."""
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/history-fund-rate",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "pageSize": str(limit),
            },
        )
        return [
            FundingRate(
                symbol=symbol,
                exchange="bitget",
                current_rate=Decimal(item["fundingRate"]),
                next_funding_time=datetime.fromtimestamp(
                    int(item["fundingTime"]) / 1000, tz=timezone.utc
                ),
                timestamp=datetime.fromtimestamp(int(item["fundingTime"]) / 1000, tz=timezone.utc),
            )
            for item in raw["data"]
        ]

    async def get_futures_funding_time(
        self: SignedRequestClient, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> datetime:
        """02b 스펙 §5.1(P1) — 다음 펀딩 정산 시각 단독 조회."""
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/funding-time",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return datetime.fromtimestamp(int(data["nextFundingTime"]) / 1000, tz=timezone.utc)

    async def get_futures_open_interest(
        self: SignedRequestClient, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Decimal:
        """02b 스펙 §5.1(P1) — FD-2.6류 시장 전체 신호 보강."""
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/open-interest",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"]
        rows = data.get("openInterestList", data) if isinstance(data, dict) else data
        row = rows[0] if isinstance(rows, list) else rows
        return Decimal(row["size"])

    async def get_futures_position_lever_tiers(
        self: SignedRequestClient, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[dict[str, Any]]:
        """02b 스펙 §5.1(P1) — 레버리지 구간표. 필드가 제각각이라 raw dict 유지."""
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/query-position-lever",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        return list(raw["data"])

    async def get_futures_current_funding_rate(
        self: SignedRequestClient, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> FundingRate:
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/current-fund-rate",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        next_time = data.get("nextUpdate")
        return FundingRate(
            symbol=symbol,
            exchange="bitget",
            current_rate=Decimal(data["fundingRate"]),
            next_funding_time=(
                datetime.fromtimestamp(int(next_time) / 1000, tz=timezone.utc)
                if next_time is not None
                else datetime.now(timezone.utc)
            ),
            timestamp=datetime.now(timezone.utc),
        )
