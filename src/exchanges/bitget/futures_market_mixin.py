"""02b_bitget_api_v2_full_spec_v1.md §5.1 — BitgetAdapter Futures Market(공개 시세) 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §5.1(P0), §9(작업 분해 3번)

`ExchangeAdapter` ABC에는 아직 없는 Bitget 전용 확장 메서드다(margin_mixin.py
모듈 docstring과 동일 원칙). `productType`(Bitget V2 무기한 선물 상품
구분자) 기본값은 `USDT-FUTURES`(가장 일반적인 USDT 담보 무기한 선물) —
06번 §6.1-A 자산군 확장 원칙에 따라 다른 상품(COIN-FUTURES/USDC-FUTURES)은
필요해지면 같은 함수에 파라미터로 그대로 전달.

엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET /api/v2/mix/market/contracts
- GET /api/v2/mix/market/ticker
- GET /api/v2/mix/market/merge-depth
- GET /api/v2/mix/market/candles
- GET /api/v2/mix/market/current-fund-rate
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle, FundingRate, OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import FuturesContractInfo
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.bitget.symbols import to_canonical_symbol as _to_canonical_symbol

DEFAULT_PRODUCT_TYPE = "USDT-FUTURES"

# AIOS 표준 timeframe -> Bitget mix candles granularity(spot과 동일 규칙,
# market_data_mixin.py의 _GRANULARITY_MAP 재사용하고 싶지만 순환 임포트
# 방지를 위해 짧은 목록이라 이 파일에 복제 — 값 자체가 바뀌면 두 곳 다
# 갱신 필요함을 docstring으로 남긴다).
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
        self, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[FuturesContractInfo]:
        raw = await self._request(  # type: ignore[attr-defined]
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
        self, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Ticker:
        raw = await self._request(  # type: ignore[attr-defined]
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
        self, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[Ticker]:
        """02b 스펙 §5.1(P0) — 전체 심볼 현재가. get_futures_ticker()의
        단일 심볼 조회와 짝을 이루는 전체 조회(문서상 별도 엔드포인트)."""
        raw = await self._request(  # type: ignore[attr-defined]
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
        self, symbol: str, *, depth: int = 20, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> OrderBook:
        raw = await self._request(  # type: ignore[attr-defined]
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
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[Candle]:
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        raw = await self._request(  # type: ignore[attr-defined]
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
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
        end_time: str | None = None,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[Candle]:
        """02b 스펙 §5.1(P0) — get_futures_candles()와 짝을 이루는 과거
        캔들 조회(FD-2.3 백테스트 데이터, market_data_mixin.py의
        get_history_candles()와 동일 목적의 Futures 버전)."""
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
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/market/history-candles", params=params
        )
        return _rows_to_candles(raw["data"], symbol, timeframe)

    async def get_futures_history_funding_rate(
        self, symbol: str, *, limit: int = 100, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[FundingRate]:
        """02b 스펙 §5.1(P1)."""
        raw = await self._request(  # type: ignore[attr-defined]
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
        self, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> datetime:
        """02b 스펙 §5.1(P1) — 다음 펀딩 정산 시각 단독 조회."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/market/funding-time",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return datetime.fromtimestamp(int(data["nextFundingTime"]) / 1000, tz=timezone.utc)

    async def get_futures_open_interest(
        self, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Decimal:
        """02b 스펙 §5.1(P1) — FD-2.6류 시장 전체 신호 보강."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/market/open-interest",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        data = raw["data"]
        rows = data.get("openInterestList", data) if isinstance(data, dict) else data
        row = rows[0] if isinstance(rows, list) else rows
        return Decimal(row["size"])

    async def get_futures_position_lever_tiers(
        self, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[dict[str, Any]]:
        """02b 스펙 §5.1(P1) — 심볼별 레버리지 구간표. 구간별 필드가
        제각각이라(최대레버리지/최대포지션/유지증거금율 등) 아직 모델화
        하지 않는다(raw dict, get_fills와 동일 판단)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/market/query-position-lever",
            params={"symbol": _to_bitget_symbol(symbol), "productType": product_type},
        )
        return list(raw["data"])

    async def get_futures_current_funding_rate(
        self, symbol: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> FundingRate:
        raw = await self._request(  # type: ignore[attr-defined]
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
