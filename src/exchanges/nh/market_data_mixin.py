"""NHAdapter Market Data 메서드군.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

엔드포인트(2026-09-03 공식 Python SDK 소스코드 확인, github.com/
PLUG-OpenAPI/nhplug-sdk): POST /krstock/quote/v1/currentPrice,
params {iem_cd, market_cd:"KRX"}.

⚠️ 응답 필드명은 SDK 스니펫에 표시돼 있지 않았다(요청 파라미터만
확인됨) — KIS(한국투자증권) API의 관례적 필드명(stck_prpr류)을
최선 추정치로 사용하되, 실제로는 전혀 다를 수 있다. 이 세션이
확인한 것은 "경로/요청 파라미터"뿐, "응답 스키마"는 라이브 검증
전까지 순수 추측이다 — Bitget/KIS보다 신뢰도가 한 단계 낮다는 걸
명시적으로 인지하고 있어야 한다. 필드가 없으면 조용히 기본값을
채우지 않고 FatalExchangeError로 실패한다(PM 배정 지침 (2)).
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
                price=Decimal(output["prpr"]),
                bid=Decimal(output.get("bidp", output["prpr"])),
                ask=Decimal(output.get("askp", output["prpr"])),
                volume_24h=Decimal(output.get("acml_vol", "0")),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentPrice 응답에 예상 필드 없음(응답 스키마 미확인 — "
                f"02e 스펙 §3 caveat 참조): {exc}"
            ) from exc

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        """02e 스펙 — 별도 호가 조회 엔드포인트를 이번 조사에서 찾지
        못했다(currentPrice 스니펫에도 경로가 없었음). currentPrice
        응답에 최우선호가가 포함돼 있을 가능성에 기대 같은 엔드포인트를
        재사용한다 — 필드가 없으면(포함 안 되는 경우) 추측 대신 명시적
        으로 실패한다."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/krstock/quote/v1/currentPrice",
            body={"iem_cd": symbol, "market_cd": _MARKET_CODE},
        )
        try:
            output = raw["Output_0"]
            bid = Decimal(output["bidp"])
            ask = Decimal(output["askp"])
        except KeyError as exc:
            raise FatalExchangeError(
                "NH currentPrice 응답에 호가 필드가 없음 — 별도 호가 조회 "
                "엔드포인트가 필요할 수 있음(02e 스펙 §3, 이번 조사에서 "
                "발견 못함): " + str(exc)
            ) from exc
        return OrderBook(
            symbol=symbol,
            exchange="nh",
            bids=[OrderBookLevel(price=bid, quantity=Decimal("0"))],
            asks=[OrderBookLevel(price=ask, quantity=Decimal("0"))],
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        """02e 스펙 §3 — `current_daily` 스니펫이 존재를 확인해줬지만
        정확한 경로/파라미터/필드명은 이번 조사에서 확보하지 못했다.
        아직 구현할 근거가 부족해 명시적으로 미구현 처리한다(추측으로
        틀린 캔들 데이터를 만드는 것보다 안전 — PM 배정 지침 (2)와
        동일 원칙)."""
        raise NotImplementedError(
            "NHAdapter.get_ohlcv: 일별시세 엔드포인트의 정확한 경로/응답 "
            "스키마가 아직 확인되지 않음(02e 스펙 §3 참조) — 라이브 문서 "
            "재확인 후 구현 필요"
        )

    async def subscribe_ticker_stream(self, symbol: str, callback: TickerCallback) -> None:
        """02e 스펙 §4 — WebSocket 접속/구독 메시지 형식은 확인했지만
        (wss://{host}:{port}/websocket, header.token + body.tr_cd="mc"),
        데이터 메시지 자체의 응답 포맷(KIS처럼 파이프 구분 텍스트인지
        순수 JSON인지)은 확인하지 못했다 — 잘못된 파서로 조용히 틀린
        Ticker를 만드는 것보다 명시적 미구현이 안전하다."""
        raise NotImplementedError(
            "NHAdapter.subscribe_ticker_stream: 실시간 데이터 메시지 포맷이 "
            "아직 확인되지 않음(02e 스펙 §4 참조) — nhplug/realtime.py 소스 "
            "추가 조사 필요"
        )
