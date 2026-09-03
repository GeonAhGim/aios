"""6.4 — BitgetAdapter Market Data(REST) 메서드군 + Private WS 로그인 서명.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§6

엔드포인트(2026-08-28 라이브 확인, GET /api/v2/spot/market/{tickers,
orderbook,candles}).

2026-09-03 task-1032(PLT-40a 선행, §9 PLT-40) — 이 파일은 원래 735줄로
P6.line_cap을 초과해 REST 메서드군 + WebSocket 연결관리/파싱/구독을 전부
갖고 있었다. 순수 이동만으로(동작 변경 0) 아래처럼 분할했다:
- `market_ws_parsing.py` — WS 메시지 순수 파싱 함수
- `market_ws_connection.py` — 연결관리 공통 루프(`_run_ws_subscription` 등)
- `market_ws_public_mixin.py` — 공개 채널 구독(ticker/candle/orderbook)
- `market_ws_private_mixin.py` — Private 채널 구독(orders/account/positions)
이 파일에는 REST Market Data 메서드군(`BitgetMarketDataMixin`)과, Private
채널 로그인 서명(`_build_login_message`, WS 로그인 전용 prehash — REST와
서명 대상이 다름)만 남긴다. 기존 테스트(`tests/unit/exchanges/
test_bitget_ws_messages.py`, `tests/integration/test_bitget_websocket.py`)가
`market_data_mixin` 모듈 경로로 직접 import하는 이름들
(`_build_login_message`/`parse_*_ws_message`/`_run_ws_subscription`/
`_send_periodic_pings`)은 무수정으로 계속 통과하도록 이 모듈에서 그대로
재-import해 노출한다(재-import는 동일 함수 객체를 가리키므로 동작 변화 없음).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.parser.candle_parser import parse_candles
from src.core.parser.orderbook_parser import parse_orderbook
from src.core.parser.ticker_parser import parse_ticker
from src.data.models.market_data import Candle, OrderBook, PublicTrade, SpotSymbolInfo, Ticker
from src.exchanges.bitget.market_ws_connection import (  # noqa: F401 — 기존 테스트 import 경로 유지
    _run_ws_subscription,
    _send_periodic_pings,
)
from src.exchanges.bitget.market_ws_parsing import (  # noqa: F401 — 기존 테스트 import 경로 유지
    parse_account_ws_message,
    parse_candle_ws_message,
    parse_order_ws_message,
    parse_orderbook_ws_message,
    parse_position_ws_message,
    parse_ticker_ws_message,
)
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient

# AIOS 표준 timeframe -> Bitget REST candles granularity 파라미터
_GRANULARITY_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


def _build_login_message(api_key: str, api_secret: str, api_passphrase: str) -> dict[str, Any]:
    """Private 채널 로그인 메시지 — 02b 스펙 §6 "Private 채널 로그인" 절
    기준 최선 추정치(공식 문서 조사, 2026-09-02). REST(`adapter.py::_sign`)와
    prehash 방식(HMAC-SHA256 후 base64)은 같지만 서명 대상 문자열이 다르다:
    REST는 실제 요청 경로/바디를 서명하는 반면, WS 로그인은 문서 관례상
    고정 문자열 "GET" + "/user/verify"를 쓴다(타임스탬프도 REST의 밀리초와
    달리 초 단위 문자열). 실제 Demo API 키로 라이브 검증 전까지 확정 아님."""
    timestamp = str(int(time.time()))
    prehash = timestamp + "GET" + "/user/verify"
    mac = hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    sign = base64.b64encode(mac.digest()).decode("utf-8")
    return {
        "op": "login",
        "args": [
            {
                "apiKey": api_key,
                "passphrase": api_passphrase,
                "timestamp": timestamp,
                "sign": sign,
            }
        ],
    }


class BitgetMarketDataMixin:
    async def get_ticker(self: SignedRequestClient, symbol: str) -> Ticker:
        raw = await self._request(
            "GET", "/api/v2/spot/market/tickers", params={"symbol": _to_bitget_symbol(symbol)}
        )
        return parse_ticker(raw["data"][0], "bitget")

    async def get_orderbook(self: SignedRequestClient, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self._request(
            "GET",
            "/api/v2/spot/market/orderbook",
            params={"symbol": _to_bitget_symbol(symbol), "limit": str(depth)},
        )
        return parse_orderbook(raw["data"], "bitget", symbol)

    async def get_ohlcv(
        self: SignedRequestClient,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[Candle]:
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        raw = await self._request(
            "GET",
            "/api/v2/spot/market/candles",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "granularity": granularity,
                "limit": str(limit),
            },
        )
        return parse_candles(raw["data"], "bitget", symbol, timeframe)

    async def get_history_candles(
        self: SignedRequestClient,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 100,
        end_time: str | None = None,
    ) -> list[Candle]:
        """02b 스펙 §3.1(P1) — FD-2.3 백테스트 데이터 확장용. `end_time`은
        Bitget 밀리초 타임스탬프 문자열(그 시점 이전 데이터 조회, 페이지네이션
        용도) — 생략 시 최신 구간부터."""
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        params: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "granularity": granularity,
            "limit": str(limit),
        }
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(
            "GET", "/api/v2/spot/market/history-candles", params=params
        )
        return parse_candles(raw["data"], "bitget", symbol, timeframe)

    async def get_symbol_info(
        self: SignedRequestClient,
        symbol: str | None = None,
    ) -> list[SpotSymbolInfo]:
        """02b 스펙 §3.1(P1)/§8 — FD-4.1(사전검증)이 필요로 하는 심볼
        규격. `symbol` 생략 시 전체 심볼 목록."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "GET", "/api/v2/spot/public/symbols", params=params or None
        )
        result = []
        for item in raw["data"]:
            price_precision = int(item.get("pricePrecision", "0"))
            quantity_precision = int(item.get("quantityPrecision", "0"))
            result.append(
                SpotSymbolInfo(
                    symbol=f"{item.get('baseCoin', '')}/{item.get('quoteCoin', '')}",
                    exchange="bitget",
                    base_coin=item.get("baseCoin", ""),
                    quote_coin=item.get("quoteCoin", ""),
                    tick_size=Decimal(1).scaleb(-price_precision),
                    lot_size=Decimal(1).scaleb(-quantity_precision),
                    min_trade_amount=Decimal(item.get("minTradeAmount", "0")),
                    status=item.get("status", ""),
                )
            )
        return result

    async def get_server_time(self: SignedRequestClient) -> datetime:
        """02b 스펙 §7(P1) — 타임스탬프 서명 오차 디버깅에 유용."""
        raw = await self._request("GET", "/api/v2/public/time")
        data = raw["data"]
        return datetime.fromtimestamp(int(data["serverTime"]) / 1000, tz=timezone.utc)

    async def get_public_trades(
        self: SignedRequestClient,
        symbol: str,
        *,
        limit: int = 100,
    ) -> list[PublicTrade]:
        """02b 스펙 §3.1(P1) — 시장 전체 체결 스트림(FD-2.6 데이터 신뢰도
        교차검증 보강용, 내 주문이 아닌 그 심볼의 전체 체결)."""
        raw = await self._request(
            "GET",
            "/api/v2/spot/market/fills",
            params={"symbol": _to_bitget_symbol(symbol), "limit": str(limit)},
        )
        return [
            PublicTrade(
                symbol=symbol,
                exchange="bitget",
                trade_id=item["tradeId"],
                price=Decimal(item["price"]),
                quantity=Decimal(item["size"]),
                side=item.get("side", ""),
                timestamp=datetime.fromtimestamp(int(item["ts"]) / 1000, tz=timezone.utc),
            )
            for item in raw["data"]
        ]
