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
from typing import Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.common.http_client import KISHTTPClient
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_canonical as _to_canonical_symbol,
)

_MARKET_CODE = "J"  # KRX


class _IntradayCandleClient(KISHTTPClient, Protocol):
    """get_ohlcv()가 같은 클래스의 _get_intraday_candles()를 호출하지만,
    self가 KISHTTPClient로 좁혀진 메서드 안에서는 그 사실이 보이지 않으므로
    명시적으로 계약에 포함한다(bitget _OrderReadingClient와 동일 패턴)."""

    async def _get_intraday_candles(self, symbol: str, *, limit: int = 100) -> list[Candle]: ...


class KISMarketDataMixin:
    async def get_ticker(self: KISHTTPClient, symbol: str) -> Ticker:
        """현재가(inquire-price)와 호가(inquire-asking-price-exp-ccn) 두
        엔드포인트를 조합한다 — KIS는 Bitget과 달리 단일 응답에 가격과
        최우선호가를 함께 주지 않는다.

        LA-19 — KRX는 원시 심볼과 canonical 표현이 같은 6자리 코드라
        변환은 필요 없지만, 형식 검증까지 생략하면 잘못된 심볼이 조용히
        거래소로 그대로 전달된다. `symbol_normalizer`(LA-7)에 검증을
        위임해 fail-closed로 만든다."""
        symbol = _to_canonical_symbol(Venue.KIS_KRX, symbol)
        price_raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": symbol},
        )
        book_raw = await self._request(
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

    async def get_orderbook(self: KISHTTPClient, symbol: str, depth: int = 20) -> OrderBook:
        symbol = _to_canonical_symbol(Venue.KIS_KRX, symbol)
        raw = await self._request(
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

    async def get_ohlcv(
        self: _IntradayCandleClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        symbol = _to_canonical_symbol(Venue.KIS_KRX, symbol)
        if timeframe == "1m":
            return await self._get_intraday_candles(symbol, limit=limit)
        if timeframe != "1d":
            raise ValueError(
                f"KISAdapter는 일봉(1d)/분봉(1m)만 지원 — '{timeframe}'. KIS "
                "inquire-time-itemchartprice는 1분 단위만 제공하고 그 외 분봉"
                "(3m/5m/...)은 거래소가 직접 주지 않는다 — 필요하면 호출부가 "
                "1분봉을 리샘플링해야 한다(02d 스펙 §2, 어댑터 스콥 밖)."
            )
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._request(
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

    async def _get_intraday_candles(
        self: KISHTTPClient, symbol: str, *, limit: int = 100
    ) -> list[Candle]:
        """02d 스펙 §2(P0) — 분봉 조회. 공식 예제(inquire_time_itemchartprice)
        기준 최선 추정치, 라이브 검증 필요. 현재 시각부터 과거 방향으로
        최대 30건까지 한 번에 내려주는 것으로 알려져 있음(문서 관례) —
        `limit`은 응답을 자르는 용도일 뿐 요청 파라미터로 직접 전달되지
        않는다."""
        now = datetime.now(timezone.utc)
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "FHKST03010200",
            params={
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": now.strftime("%H%M%S"),
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        rows = raw.get("output2", [])[:limit]
        candles = []
        for row in rows:
            day = row.get("stck_bsop_date", now.strftime("%Y%m%d"))
            hour = row["stck_cntg_hour"]  # "HHMMSS"
            open_time = datetime.strptime(day + hour, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=symbol,
                    exchange="kis",
                    timeframe="1m",
                    open=Decimal(row["stck_oprc"]),
                    high=Decimal(row["stck_hgpr"]),
                    low=Decimal(row["stck_lwpr"]),
                    close=Decimal(row["stck_prpr"]),
                    volume=Decimal(row["cntg_vol"]),
                    open_time=open_time,
                    close_time=open_time,
                )
            )
        return candles

    async def is_market_holiday(self: KISHTTPClient, date: str) -> bool:
        """02d 스펙 §2(P0) — `date`는 "YYYYMMDD". MarketHours 정확도
        보강용(현재 get_capabilities()의 고정 스케줄은 공휴일을 모른다).
        공식 예제(chk_holiday) 기준 최선 추정치, 라이브 검증 필요."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/chk-holiday",
            "CTCA0903R",
            params={"BASS_DT": date, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        )
        rows = raw.get("output", [])
        if not rows:
            return False
        return bool(rows[0].get("opnd_yn") == "N")

