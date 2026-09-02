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
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.common.types import TickerCallback

_MARKET_CODE = "KRX"


class NHMarketDataMixin:
    async def get_ticker(self, symbol: str) -> Ticker:
        raw = await self._request(  # type: ignore[attr-defined]
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

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        """공식 openapi.json 확인 — 별도 호가 조회 엔드포인트는 없고
        currentPrice 응답에 10단계 호가(askp1..10/bidp1..10, 잔량
        askp_rsqn{1..10}/bidp_rsqn{1..10})가 함께 내려온다(모듈 docstring
        참조) — 이전 세션의 "재사용 추정"이 맞았고, 이번에 1호가 전용
        가짜 depth(quantity=0)에서 실제 10단계 depth로 승격한다."""
        raw = await self._request(  # type: ignore[attr-defined]
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

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        """02e 스펙 §4 — 2026-09-03(task-114) 재확인: 공식 SDK 소스코드
        (nhplug/realtime.py)로 접속(wss://{host}:{port}/websocket)·구독
        메시지(header.token + body.tr_cd)·재연결까지 확인했고
        websocket_mixin.py의 `connect_and_subscribe()`로 구현했다. 다만
        **데이터 프레임의 `body` 내부 필드 스키마**(채널별 실제 필드명)는
        SDK가 파싱을 호출부에 위임해 여전히 미확인이다 — 잘못된 파서로
        조용히 틀린 Ticker를 만드는 것보다 명시적 미구현이 안전하다
        (websocket_mixin.py 모듈 docstring 참조)."""
        raise NotImplementedError(
            "NHAdapter.subscribe_ticker_stream: 연결/구독은 구현됨"
            "(websocket_mixin.connect_and_subscribe) — 데이터 프레임 필드 "
            "추가 조사 필요"
        )
