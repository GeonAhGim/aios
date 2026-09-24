# ratchet-allow: unverified-endpoint fields raise NotImplementedError instead of guessing (I2)
"""NHAdapter Market Data 메서드군.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

엔드포인트(2026-09-03 재확인, task-114): POST /krstock/quote/v1/currentPrice,
params {iem_cd, market_cd:"KRX"}.

**응답 필드명 확인**(공식 OpenAPI 스펙 `https://www.nhplug.com/openapi-docs/
krstock/openapi.json`을 도메인(SSOT)에서 직접 내려받아 확인 — 이전 세션은
SDK 스니펫에 요청 파라미터만 있어 응답 필드를 KIS 관례로 추정했었다):
- **현재가는 `stck_prpr`** — 이전 추정 `prpr`은 실제로 존재하지 않는
  필드명이었다(있었다면 항상 FatalExchangeError로 실패했을 것).
- 매도/매수 1호가는 `askp`/`bidp`(추정과 일치), 거래량은 `acml_vol`(일치).
- 호가 10단계 전체(`askp1..10`/`bidp1..10`, 잔량 `askp_rsqn{1..10}`/
  `bidp_rsqn{1..10}`)도 같은 응답에 포함된다 — 별도 호가 조회 엔드포인트가
  없다는 이전 추정이 맞았다(currentPrice가 시세+호가를 겸함).

`subscribe_ticker_stream()` (2026-09-16, task-2615): see the
`websocket_parsing.py` module docstring -- the official openapi.json's
`x-realtime-channels` confirmed the `tr_cd="mc"` channel's data-frame
field schema, so it no longer needs to stay fail-closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.common.types import TickerCallback
from src.exchanges.nh.websocket_mixin import ConnectFn, RawFrameHandler, _connect
from src.exchanges.nh.websocket_parsing import parse_mc_ticker_frame

_MARKET_CODE = "KRX"
_TICKER_TR_CD = "mc"


class _WebSocketSubscribingClient(Protocol):
    """`subscribe_ticker_stream()` calls `NHWebSocketMixin.
    connect_and_subscribe()` on the same instance, but that contract isn't
    visible from within this file, so it is declared explicitly here (same
    pattern as `_BalanceReadingClient` in trading_mixin.py)."""

    async def connect_and_subscribe(
        self,
        tr_cd: str,
        tr_key: str,
        on_raw_frame: RawFrameHandler,
        *,
        is_domestic: bool = True,
        connect_fn: ConnectFn = _connect,
    ) -> None: ...


class NHMarketDataMixin:
    async def get_ticker(self: NHHTTPClient, symbol: str) -> Ticker:
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentPrice",
            body={"iem_cd": symbol, "market_cd": _MARKET_CODE},
        )
        try:
            output = raw["Output_0"]
            return Ticker(
                symbol=symbol,
                exchange="nh",
                price=Decimal(str(output["stck_prpr"])),
                bid=Decimal(str(output.get("bidp", output["stck_prpr"]))),
                ask=Decimal(str(output.get("askp", output["stck_prpr"]))),
                volume_24h=Decimal(str(output.get("acml_vol", "0"))),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentPrice 응답에 예상 필드 없음(공식 openapi.json 기준 "
                f"stck_prpr 필요, 02e 스펙 §3 참조): {exc}"
            ) from exc

    async def get_orderbook(self: NHHTTPClient, symbol: str, depth: int = 20) -> OrderBook:
        """공식 openapi.json 확인 — 별도 호가 조회 엔드포인트는 없고
        currentPrice 응답에 10단계 호가(askp1..10/bidp1..10, 잔량
        askp_rsqn{1..10}/bidp_rsqn{1..10})가 함께 내려온다(모듈 docstring
        참조) — 이전 세션의 "재사용 추정"이 맞았고, 이번에 1호가 전용
        가짜 depth(quantity=0)에서 실제 10단계 depth로 승격한다."""
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentPrice",
            body={"iem_cd": symbol, "market_cd": _MARKET_CODE},
        )
        try:
            output = raw["Output_0"]
            bids = [
                OrderBookLevel(
                    price=Decimal(str(output[f"bidp{i}"])),
                    quantity=Decimal(str(output[f"bidp_rsqn{i}"])),
                )
                for i in range(1, 11)
                if output.get(f"bidp{i}")
            ]
            asks = [
                OrderBookLevel(
                    price=Decimal(str(output[f"askp{i}"])),
                    quantity=Decimal(str(output[f"askp_rsqn{i}"])),
                )
                for i in range(1, 11)
                if output.get(f"askp{i}")
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentPrice 응답에 호가 필드가 없음(공식 openapi.json 기준 "
                f"askp{{1..10}}/bidp{{1..10}} 필요, 02e 스펙 §3 참조): {exc}"
            ) from exc
        if not bids or not asks:
            raise FatalExchangeError(f"NH currentPrice 응답에 호가 잔량이 없음: {symbol}")
        return OrderBook(
            symbol=symbol,
            exchange="nh",
            bids=bids[:depth],
            asks=asks[:depth],
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(
        self: NHHTTPClient, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        """주식 일별 OHLCV 데이터 조회 — POST /krstock/quote/v1/currentDaily.

        Task-6695(BR-17): 공식 openapi.json에서 요청/응답 스키마 확인 완료.
        - 요청: Input_0.iem_cd(종목코드), market_cd("KRX"), view_main_yn("Y"),
          array_cnt(개수, 선택)
        - 응답: Output_0[]로 배열(각 항목 = 1일 데이터)
        - 필드: bsop_date(거래일), stck_oppr(시가), stck_hgpr(고가),
          stck_lwpr(저가), stck_clpr(종가), acml_vol(누적거래량)

        timeframe 파라미터는 adapter 계약에서 필요하나, NH API는 항상
        일별("1d") 데이터만 제공한다. 다른 timeframe 요청 시 ValueError.
        """
        if timeframe != "1d":
            raise ValueError(
                f"NHAdapter.get_ohlcv: 일별(1d) 데이터만 지원. "
                f"요청: {timeframe}. 다른 timeframe은 후속 리프 또는 다른 API 필요."
            )

        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentDaily",
            body={
                "iem_cd": symbol,
                "market_cd": _MARKET_CODE,
                "view_main_yn": "Y",
                "array_cnt": limit,
            },
        )
        try:
            candles: list[Candle] = []
            for item in raw.get("Output_0", []):
                # bsop_date format: "YYYYMMDD" (예: "20260924")
                date_str = item["bsop_date"]
                # Parse as YYYYMMDD and create midnight UTC timestamp
                year = int(date_str[:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                candle_date = datetime(year, month, day, tzinfo=timezone.utc)

                candle = Candle(
                    symbol=symbol,
                    exchange="nh",
                    timeframe="1d",
                    open=Decimal(str(item.get("stck_oppr", item["stck_clpr"]))),
                    high=Decimal(str(item["stck_hgpr"])),
                    low=Decimal(str(item["stck_lwpr"])),
                    close=Decimal(str(item["stck_clpr"])),
                    volume=Decimal(str(item.get("acml_vol", "0"))),
                    open_time=candle_date,
                    close_time=candle_date,
                )
                candles.append(candle)
            return candles
        except (KeyError, ValueError) as exc:
            raise FatalExchangeError(
                f"NH currentDaily 응답 파싱 오류(공식 openapi.json 기준 필수 필드: "
                f"bsop_date, stck_oppr, stck_hgpr, stck_lwpr, stck_clpr, acml_vol): {exc}"
            ) from exc

    async def subscribe_ticker_stream(
        self: _WebSocketSubscribingClient,
        symbol: str,
        callback: TickerCallback,
        *,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02e spec S4 -- 2026-09-16 (task-2615) re-confirmed: the official
        asset-class openapi.json's `x-realtime-channels` confirmed the
        `body` field schema of `tr_cd="mc"` (domestic consolidated
        real-time trade price) data frames (see websocket_parsing.py
        module docstring, docs/exchanges/NH_GAPS.md S2). The earlier
        session's (task-114) "unconfirmed, SDK delegates parsing"
        conclusion only held for the SDK source; the openapi.json itself
        actually has per-channel field lists and examples. `mb` (order
        book) / `d2` (execution notice) channels also have a confirmed
        schema but no consuming method yet, so they are out of this
        leaf's scope (NH_GAPS.md S2-3). `connect_fn` is test-injection
        only, same as KIS's `subscribe_ticker_stream()` (default is a
        real WebSocket connection)."""

        async def on_raw_frame(raw: str) -> None:
            ticker = parse_mc_ticker_frame(raw)
            if ticker is not None:
                await callback(ticker)

        await self.connect_and_subscribe(_TICKER_TR_CD, symbol, on_raw_frame, connect_fn=connect_fn)
