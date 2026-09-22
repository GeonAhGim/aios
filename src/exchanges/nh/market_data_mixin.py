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

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        """02e 스펙 §3 — 2026-09-03(task-114) 재확인: 공식 openapi.json으로
        경로 자체는 `/krstock/quote/v1/currentDaily`로 확인됐지만, 이번
        리프의 스콥(정정/취소/주문조회 + WS)에는 없어 요청 파라미터/응답
        스키마까지는 조사하지 않았다. 아직 구현할 근거가 부족해 명시적으로
        미구현 처리한다(추측으로 틀린 캔들 데이터를 만드는 것보다 안전 —
        PM 배정 지침 (2)와 동일 원칙)."""
        raise NotImplementedError(
            "NHAdapter.get_ohlcv: 경로는 확인됨(/krstock/quote/v1/currentDaily, "
            "공식 openapi.json) — 요청/응답 스키마는 아직 조사 안 됨(02e 스펙 "
            "§3 참조), 후속 리프에서 구현 필요"
        )

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
