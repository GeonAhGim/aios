"""6.10 — KISAdapter Market Data 메서드군(조회성 API).

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 KIS 공식 GitHub 예제 소스코드 확인):
- GET /uapi/domestic-stock/v1/quotations/inquire-price, tr_id FHKST01010100
  (현재가·거래량 — bid/ask 없음)
- GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn,
  tr_id FHKST01010200 (호가 — askp1/bidp1)
- GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice,
  tr_id FHKST03010100 (일/주/월/년봉만 — 분봉 없음, 분봉은 별도
  inquire-time-itemchartprice 엔드포인트 필요, 아직 미구현)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.common.types import TickerCallback

_MARKET_CODE = "J"  # KRX


class KISMarketDataMixin:
    async def get_ticker(self, symbol: str) -> Ticker:
        """현재가(inquire-price)와 호가(inquire-asking-price-exp-ccn) 두
        엔드포인트를 조합한다 — KIS는 Bitget과 달리 단일 응답에 가격과
        최우선호가를 함께 주지 않는다."""
        price_raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": symbol},
        )
        book_raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": symbol},
        )
        try:
            price_output = price_raw["output"]
            book_output1 = book_raw["output1"]
            return Ticker(
                symbol=symbol,
                exchange="kis",
                price=Decimal(price_output["stck_prpr"]),
                bid=Decimal(book_output1["bidp1"]),
                ask=Decimal(book_output1["askp1"]),
                volume_24h=Decimal(price_output["acml_vol"]),
                timestamp=datetime.now(timezone.utc),  # KIS 응답에 별도 타임스탬프 필드 없음
                source_type="primary",
            )
        except KeyError as exc:
            raise FatalExchangeError(f"KIS ticker 응답에 예상 필드 없음: {exc}") from exc

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": symbol},
        )
        try:
            output1 = raw["output1"]
            bids = [
                OrderBookLevel(
                    price=Decimal(output1[f"bidp{i}"]),
                    quantity=Decimal(output1[f"bidp_rsqn{i}"]),
                )
                for i in range(1, min(depth, 10) + 1)
                if f"bidp{i}" in output1
            ]
            asks = [
                OrderBookLevel(
                    price=Decimal(output1[f"askp{i}"]),
                    quantity=Decimal(output1[f"askp_rsqn{i}"]),
                )
                for i in range(1, min(depth, 10) + 1)
                if f"askp{i}" in output1
            ]
        except KeyError as exc:
            raise FatalExchangeError(f"KIS orderbook 응답에 예상 필드 없음: {exc}") from exc

        return OrderBook(
            symbol=symbol,
            exchange="kis",
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        if timeframe != "1d":
            raise ValueError(
                f"KISAdapter는 현재 일봉(1d)만 지원 — '{timeframe}'은 별도 분봉 엔드포인트 "
                "(inquire-time-itemchartprice) 미구현(6.9/6.10 스콥 밖)"
            )
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": "19000101",
                "FID_INPUT_DATE_2": today,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "1",
            },
        )
        rows = raw.get("output2", [])[:limit]
        candles = []
        for row in rows:
            day = row["stck_bsop_date"]  # "YYYYMMDD"
            open_time = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=symbol,
                    exchange="kis",
                    timeframe="1d",
                    open=Decimal(row["stck_oprc"]),
                    high=Decimal(row["stck_hgpr"]),
                    low=Decimal(row["stck_lwpr"]),
                    close=Decimal(row["stck_clpr"]),
                    volume=Decimal(row["acml_vol"]),
                    open_time=open_time,
                    close_time=open_time,
                )
            )
        return candles

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        """KIS WebSocket(승인키 기반 별도 인증 체계)은 6.9/6.10 스콥 밖 —
        get_capabilities().supports_websocket=False이므로 02번 §2.1 원칙대로
        NotImplementedError를 발생시키고 호출부가 REST 폴링으로 폴백한다."""
        raise NotImplementedError("KISAdapter는 아직 WebSocket 실시간 구독을 지원하지 않음")
