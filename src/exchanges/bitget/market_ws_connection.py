"""02b_bitget_api_v2_full_spec_v1.md §6 — Bitget WebSocket 연결관리 공통 루프.

2026-09-02 리팩터링(02b 스펙 §9 작업 분해 4번, WebSocket P0) — 기존
`subscribe_ticker_stream()`은 연결관리(재연결·백오프)와 메시지 파싱이
한 함수 안에 뒤섞여 있어 실소켓 없이는 전혀 테스트할 수 없었다. 연결관리
공통 루프(`_run_ws_subscription()`)만 이 모듈에 모아, `connect_fn`을
주입 가능하게 열어둬(기본값은 실제 websockets.connect) 가짜 연결로
재연결/백오프 동작까지 결정적으로 재현할 수 있다. 메시지 파싱은
`market_ws_parsing.py` 참조.

2026-09-03 task-1032(PLT-40a 선행) — `market_data_mixin.py`(735줄, P6
line_cap 초과)에서 순수 이동(동작 변경 0). 기존 테스트가 참조하는 모듈
경로(`market_data_mixin`)는 그 파일에서 이 모듈의 이름들을 재-import해
그대로 유지한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

ReconnectHook = Callable[[], Awaitable[None]]
MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """`websockets.asyncio.client.connect`는 실제로는 URL 하나만으로도
    호출 가능한 비동기 컨텍스트 매니저를 반환하지만, 클래스 자체의 타입
    시그니처는 그보다 훨씬 넓다(헤더/ping 설정 등) — 테스트가 주입하는
    가짜 `connect_fn`과 정확히 같은 좁은 타입으로 맞추기 위한 얇은 래퍼."""
    return _default_connect(url)  # type: ignore[return-value]


_PING_INTERVAL_SECONDS = 30.0
_PING_MESSAGE = "ping"
_PONG_MESSAGE = "pong"


async def _send_periodic_pings(
    ws: WsConnection,
    interval: float,
    ping_sleep_fn: Callable[[float], Awaitable[None]],
) -> None:
    """FULL_AUDIT_2026-09-02.md §2-B ② — Bitget 공식 문서 관례: 30초
    이상 아무 메시지도 오가지 않으면 서버가 연결을 끊는다. 클라이언트가
    주기적으로 평문 "ping"(JSON 아님 — 다른 메시지들과 다른 프로토콜)을
    보내고 "pong"을 돌려받아야 유지된다. `_run_ws_subscription()`이
    메시지 수신 루프와 나란히 백그라운드 태스크로 돌린다."""
    while True:
        await ping_sleep_fn(interval)
        await ws.send(_PING_MESSAGE)


async def _run_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_message: MessageHandler,
    *,
    pre_messages_factory: Callable[[], list[dict[str, Any]]] | None = None,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ping_interval_seconds: float = _PING_INTERVAL_SECONDS,
    ping_sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """연결·구독·재연결(지수 백오프)을 전담하는 공통 루프 — 메시지
    자체의 의미는 모른다(on_message에 그대로 위임). 채널이 몇 개든
    이 루프 하나를 재사용한다(§2.1 재연결 책임 원칙, 로직 중복 방지).
    `pre_messages_factory`(예: Private 채널의 login)는 매 연결(최초 포함,
    재연결 포함) *시작될 때마다 새로 호출돼* subscribe_msg보다 먼저
    순서대로 전송된다.

    레드팀 #2026-09-02-31 — 이전엔 고정 `list[dict]`를 받아 재연결마다
    같은(오래된 타임스탬프로 서명된) 메시지를 재전송했다 — Bitget의 WS
    로그인 서명은 REST처럼 타임스탬프가 일정 범위 안이어야 유효하므로,
    최초 연결 이후 재연결부터는 반드시 실패한다. 팩토리로 바꿔 매
    시도마다 새로 서명하게 한다.

    `ping_sleep_fn`은 `sleep_fn`(재연결 백오프 대기)과 고의로 분리했다 —
    같은 걸 썼다면 백오프 테스트가 주입하는 가짜 즉시-완료 sleep이
    ping 루프도 즉시 무한 반복시켜 테스트를 오염시킨다(핑 간격은 실제
    시간 기준이 맞다 — 백오프와는 별개 개념)."""
    backoff = 1.0
    first_attempt = True

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                for pre_message in (pre_messages_factory() if pre_messages_factory else []):
                    await ws.send(json.dumps(pre_message))
                await ws.send(json.dumps(subscribe_msg))
                if backoff > 1.0 and on_reconnected is not None:
                    await on_reconnected()
                backoff = 1.0
                ping_task = asyncio.ensure_future(
                    _send_periodic_pings(ws, ping_interval_seconds, ping_sleep_fn)
                )
                try:
                    async for raw_message in ws:
                        # FULL_AUDIT §2-B ② — 이전엔 모든 수신 메시지를
                        # 무조건 json.loads()로 파싱했다. Bitget이 우리
                        # ping에 대한 응답으로 평문 "pong"을 보내면(공식
                        # 문서 관례) JSON이 아니라서 예외가 나 연결
                        # 전체가 죽었을 것이다.
                        if raw_message == _PONG_MESSAGE:
                            continue
                        message = json.loads(raw_message)
                        event = message.get("event")
                        if event == "login":
                            # 레드팀 #2026-09-02-31 — 이전엔 로그인 성공/실패를
                            # 전혀 구분하지 않고 각 parse_*_ws_message가 조용히
                            # 버려서(_is_control_message), 재연결 후 인증이
                            # 깨져도 로그 한 줄 안 남았다.
                            if str(message.get("code", "0")) in ("0", "None"):
                                logger.info(
                                    "Bitget WS 로그인 성공(channel=%s)", subscribe_msg.get("args")
                                )
                            else:
                                logger.warning(
                                    "Bitget WS 로그인 실패(channel=%s): %s — private 채널 "
                                    "데이터를 받지 못하고 있을 수 있습니다.",
                                    subscribe_msg.get("args"),
                                    message,
                                )
                            continue
                        if event == "subscribe":
                            # FULL_AUDIT §2-B ② — 구독 ack도 로그인처럼
                            # 성공/실패 구분 없이 조용히 버려지고 있었다.
                            logger.info(
                                "Bitget WS 구독 성공(channel=%s)", message.get("arg")
                            )
                            continue
                        if event == "error":
                            logger.warning(
                                "Bitget WS 오류 이벤트(channel=%s): %s",
                                subscribe_msg.get("args"),
                                message,
                            )
                            continue
                        await on_message(message)
                finally:
                    ping_task.cancel()
                    try:
                        await ping_task
                    except asyncio.CancelledError:
                        pass
        except (ConnectionClosed, OSError) as exc:
            logger.warning(
                "Bitget WS 연결 끊김(channel=%s): %s — %.1f초 후 재연결",
                subscribe_msg.get("args"),
                exc,
                backoff,
            )
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
