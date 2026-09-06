"""02d_kis_api_full_spec_v1.md §6 — KISAdapter WebSocket(실시간) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §6, §7(작업 분해 6번)

기존 `subscribe_ticker_stream()`은 `NotImplementedError`로 막혀 있었다
(승인키 인증 체계 미확인, 6.9/6.10 스콥 밖으로 문서화됨) — 이번 조사
(WebFetch, github.com/koreainvestment/open-trading-api/examples_user/
{kis_auth.py, domestic_stock/domestic_stock_functions_ws.py},
2026-09-02)로 실제 예제 소스코드를 직접 확인해 구현한다.

**메시지 형식(공식 예제 파싱 코드로 직접 확인, 높은 신뢰도)**:
- 제어 메시지(구독 ack, PINGPONG)는 JSON — `{"header": {...}, "body": {...}}`
- 데이터 메시지는 파이프(`|`) 4단 분할: `암호화플래그|tr_id|데이터건수|본문`,
  본문 내부는 캐럿(`^`)으로 필드 구분 — `raw.split("|")`, `body.split("^")`가
  공식 예제 파싱 로직 그대로.
- PINGPONG: 헤더의 tr_id가 "PINGPONG"이면 받은 메시지를 그대로 pong
  프레임으로 돌려보내야 한다(`ws.pong(raw)`, 공식 예제 확인) — 안 하면
  서버가 연결을 끊는다.
- 체결통보(H0STCNI0/데모 H0STCNI9)는 **암호화**된다 — 구독 ack의
  `body.output.key`/`body.output.iv`(AES-256-CBC)로 이후 데이터를
  복호화해야 한다(공식 예제 docstring + 파싱 코드로 확인). 이 세션은
  AES 루틴 자체는 왕복 테스트로 검증했지만, KIS 서버가 실제로 내려주는
  key/iv 인코딩(원문 그대로인지 base64인지 등)은 라이브 응답으로만
  확정 가능 — 최선 추정치(공식 예제 관례: 원문 문자열 그대로 UTF-8
  인코딩해 키로 사용).

task-1723 P1-D: 원래 422줄(P6 300줄 초과)이던 이 모듈을 순수 이동으로
분할했다 — 파싱 로직은 websocket_parsing.py, 연결관리/재연결 루프는
websocket_connection.py. 공개 심볼(테스트가 이 모듈 경로로 직접 import하는
`_PRICE_FIELDS` 등 포함)은 아래 재수출로 그대로 유지한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.data.models.market_data import OrderBook
from src.data.models.trading import Order
from src.exchanges.common.types import TickerCallback
from src.exchanges.kis.websocket_connection import (
    ConnectFn,
    MessageHandler,
    ReconnectHook,
    WsConnection,
    _connect,
    _run_kis_ws_subscription,
)
from src.exchanges.kis.websocket_parsing import (
    _ORDER_NOTICE_FIELDS,
    _ORDERBOOK_FIELDS,
    _PRICE_FIELDS,
    _build_orderbook_fields,
    _build_subscribe_message,
    _is_json_message,
    _split_records,
    _split_ws_frame,
    decrypt_aes256_cbc,
    parse_order_notification_message,
    parse_realtime_orderbook_message,
    parse_realtime_price_message,
)

__all__ = [
    "_ORDER_NOTICE_FIELDS",
    "_ORDERBOOK_FIELDS",
    "_PRICE_FIELDS",
    "_build_orderbook_fields",
    "_build_subscribe_message",
    "_connect",
    "_is_json_message",
    "_run_kis_ws_subscription",
    "_split_records",
    "_split_ws_frame",
    "ConnectFn",
    "KISWebSocketMixin",
    "MessageHandler",
    "OrderBookCallback",
    "OrderCallback",
    "ReconnectHook",
    "WsConnection",
    "WS_PAPER_URL",
    "WS_REAL_URL",
    "decrypt_aes256_cbc",
    "parse_order_notification_message",
    "parse_realtime_orderbook_message",
    "parse_realtime_price_message",
]

WS_REAL_URL = "ws://ops.koreainvestment.com:21000"
WS_PAPER_URL = "ws://ops.koreainvestment.com:31000"

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
OrderCallback = Callable[[Order], Awaitable[None]]


class KISWebSocketMixin:
    async def get_ws_approval_key(self) -> str:
        """REST 접근토큰(`_ensure_token()`)과 별개의 WS 전용 승인키 —
        `secretkey` 필드명이 REST의 `appsecret`과 다름(공식 예제 확인)."""
        response = await self._client.post(  # type: ignore[attr-defined]
            "/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,  # type: ignore[attr-defined]
                "secretkey": self._app_secret,  # type: ignore[attr-defined]
            },
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        return str(response.json()["approval_key"])

    async def subscribe_ticker_stream(
        self,
        symbol: str,
        callback: TickerCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02d 스펙 §6(P0) — 기존 NotImplementedError를 실제 구현으로
        대체(승인키 인증 확인 완료, 모듈 docstring 참조)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, "H0STCNT0", symbol)

        async def on_data_frame(raw: str) -> None:
            for ticker in parse_realtime_price_message(raw):
                await callback(ticker)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_orderbook_stream(
        self,
        symbol: str,
        callback: OrderBookCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """`ExchangeAdapter` ABC에는 아직 없음(Bitget 확장 메서드들과
        동일 원칙 — 소비하는 FD-2 호출부가 생기기 전까지 KIS 전용)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, "H0STASP0", symbol)

        async def on_data_frame(raw: str) -> None:
            book = parse_realtime_orderbook_message(raw)
            if book is not None:
                await callback(book)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_order_notification_stream(
        self,
        callback: OrderCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02d 스펙 §6(P1) — FD-4.5류 재조회를 실시간으로 대체할 후보
        (Bitget WS orders 채널과 동일 가치). **암호화 채널**이라 다른
        두 스트림보다 신뢰도가 낮다 — 모듈 docstring의 AES 관련 caveat
        참조. `tr_key`는 문서 관례상 공백(계좌 전체 대상)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        tr_id = "H0STCNI9" if self._is_paper_trading else "H0STCNI0"  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, tr_id, "")

        key_iv: dict[str, str] = {}

        def on_key_iv(key: str, iv: str) -> None:
            key_iv["key"] = key
            key_iv["iv"] = iv

        async def on_data_frame(raw: str) -> None:
            if "key" not in key_iv:
                return
            order = parse_order_notification_message(raw, key=key_iv["key"], iv=key_iv["iv"])
            if order is not None:
                await callback(order)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            on_key_iv=on_key_iv,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
