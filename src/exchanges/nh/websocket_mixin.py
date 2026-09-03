"""NHAdapter WebSocket 구독 인프라.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§4

2026-09-03(task-114) — 공식 SDK 소스코드(`nhplug/realtime.py`,
github.com/PLUG-OpenAPI/nhplug-sdk, 도메인 명세가 SSOT)를 직접 확인해
이전 세션의 "데이터 프레임 형식 미확인" 상태를 해소했다.

**확인**(realtime.py 모듈 docstring 그대로):
- 접속: `wss://<host>:<port>/websocket` — 경로 `/websocket`이 필수(빠지면
  핸드셰이크 거부/구독 무시).
- 포트: 시세 국내 7070 / 해외 7080, 통보(체결·주문내역)는 국내·해외 모두
  7070, 모의투자는 국내·해외 공통 17070.
- 구독: `{"header":{"token":TOKEN,"tr_type":"1"},"body":{"tr_cd":<채널>,
  "tr_key":<종목코드 등>}}`, 해제는 tr_type="2".
- 푸시(데이터) 프레임: `{"header":{tr_cd,tr_key},"body":{...}}` — **JSON**
  (KIS처럼 파이프 구분 텍스트가 아니다), heartbeat 불필요, 암호화 없음.
  구독 ack와 데이터 푸시는 헤더에 `tr_type`/`rsp_cd`가 있는지로 구분한다
  (있으면 ack, 없으면 데이터 — realtime.py 소스 확인).

**아직 미확인** — `body` 내부 필드 스키마(채널별 실제 필드명, 예: 체결가
채널 "mc"의 가격 필드가 REST(`stck_prpr`)와 같은 이름을 쓰는지)는
`nhplug-sdk`가 파싱을 호출부(`on_message` 콜백)에 위임해 SDK 자체에
문서화돼 있지 않다 — 자산군 `openapi.json`의 `x-realtime-channels`까지
확인해야 확정된다(다음 리프 후보). 그래서 이 모듈은 "연결·구독·재연결"
책임만 구현하고, 수신한 원시 JSON 프레임은 파싱하지 않은 채 호출부
콜백에 그대로 전달한다 — 필드를 추측해 Ticker로 매핑하면 근거 없는
변환을 성공으로 위장하는 것이 된다(PM 배정 지침 (2)). 그래서
`ExchangeAdapter.subscribe_ticker_stream()`(market_data_mixin.py)은 아직
이 모듈을 쓰지 않고 NotImplementedError를 유지한다 — `connect_and_subscribe()`
는 라이브 검증(실제 프레임 캡처)과 향후 파싱기 개발을 위한 확장 메서드로,
ABC 계약에는 없다(KIS의 `get_ws_approval_key()`/`subscribe_orderbook_stream()`
과 동일한 위치 — 확인된 범위만 정직하게 구현).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

WS_DOMESTIC_URL = "wss://api.nhplug.com:7070/websocket"
WS_OVERSEAS_URL = "wss://api.nhplug.com:7080/websocket"
WS_PAPER_URL = "wss://moapi.nhplug.com:17070/websocket"

ReconnectHook = Callable[[], Awaitable[None]]
RawFrameHandler = Callable[[str], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """`websockets.asyncio.client.connect()`가 반환하는 `Connect`는
    구조적으로 `WsConnection`(send/__aiter__)을 만족하는 `ClientConnection`을
    내지만, mypy는 라이브러리 반환 타입 자체를 `AbstractAsyncContextManager`의
    서브타입으로 인식하지 못한다(PLT-40c 조사 확인) — `cast()`는 런타임에
    아무 동작도 하지 않으므로(단순 타입 단언) 기존 `# type: ignore[return-value]`
    와 동일하게 무해하다."""
    return cast(AbstractAsyncContextManager[WsConnection], _default_connect(url))


def build_subscribe_message(
    token: str, tr_cd: str, tr_key: str, *, tr_type: str = "1"
) -> dict[str, Any]:
    """02e 스펙 §4 확인된 구독 메시지 봉투. `tr_type` "1"=등록, "2"=해제."""
    return {
        "header": {"token": token, "tr_type": tr_type},
        "body": {"tr_cd": tr_cd, "tr_key": tr_key},
    }


async def _run_nh_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_raw_frame: RawFrameHandler,
    *,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """KIS `_run_kis_ws_subscription()`(websocket_mixin.py)과 동일한
    연결관리/재연결 책임(§2.1 재연결 책임 원칙 — 형태가 아니라 책임의
    재사용). NH 전용 제어 프레임(PINGPONG 등)은 공식 소스에서 heartbeat가
    불필요하다고 확인됐으므로 넣지 않는다."""
    backoff = 1.0
    first_attempt = True

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                await ws.send(json.dumps(subscribe_msg))
                if backoff > 1.0 and on_reconnected is not None:
                    await on_reconnected()
                backoff = 1.0
                async for raw in ws:
                    await on_raw_frame(raw)
        except (ConnectionClosed, OSError) as exc:
            logger.warning("NH WS 연결 끊김: %s — %.1f초 후 재연결", exc, backoff)
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)


class _TokenizedClient(Protocol):
    """connect_and_subscribe()가 `_NHHTTPClient`(adapter.py)의 토큰 캐싱/
    모의투자 판별 상태에 접근한다 — `_request`/`_act_no`(NHHTTPClient)와는
    무관한 별개 계약이라 공용 http_client.py에 넣지 않고 이 파일에
    로컬로 좁혀 선언한다(bitget `_TickerReadingClient` 등과 동일한
    "파일-로컬 확장 Protocol" 패턴)."""

    _is_paper_trading: bool

    async def _ensure_token(self) -> str: ...


class NHWebSocketMixin:
    async def connect_and_subscribe(
        self: _TokenizedClient,
        tr_cd: str,
        tr_key: str,
        on_raw_frame: RawFrameHandler,
        *,
        is_domestic: bool = True,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02e 스펙 §4(P1) — 연결/구독/재연결까지 확인된 범위로 구현한다
        (모듈 docstring 참조). 데이터 프레임 파싱은 아직 하지 않으므로
        호출부가 `on_raw_frame`으로 원시 JSON 문자열을 직접 받는다."""
        token = await self._ensure_token()
        if self._is_paper_trading:
            url = WS_PAPER_URL
        else:
            url = WS_DOMESTIC_URL if is_domestic else WS_OVERSEAS_URL
        subscribe_msg = build_subscribe_message(token, tr_cd, tr_key)

        await _run_nh_ws_subscription(
            url,
            subscribe_msg,
            on_raw_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
