"""02d_kis_api_full_spec_v1.md §6 — KIS WS 연결관리(재연결/백오프/PINGPONG).

task-1723 P1-D: websocket_mixin.py(422줄, P6 300줄 초과)에서 연결 프로토콜과
재연결 루프를 분리. 공개 심볼은 websocket_mixin.py가 그대로 재수출한다.
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

from src.exchanges.kis.websocket_parsing import _is_json_message

logger = logging.getLogger(__name__)

ReconnectHook = Callable[[], Awaitable[None]]
MessageHandler = Callable[[str], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def pong(self, data: bytes | str = b"") -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    return _default_connect(url)  # type: ignore[return-value]


async def _run_kis_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_data_frame: MessageHandler,
    *,
    on_key_iv: Callable[[str, str], None] | None = None,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Bitget의 `_run_ws_subscription()`(market_data_mixin.py)과 동일한
    연결관리/재연결/백오프 책임을 지지만, KIS는 메시지 형식 자체가
    근본적으로 달라(JSON 제어 vs 파이프 데이터, PINGPONG 필수 응답)
    같은 함수를 재사용할 수 없다 — 별도 구현(§2.1 재연결 책임 원칙은
    로직 형태가 아니라 "책임"의 재사용, 코드 재사용까지 강제하지 않음)."""
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
                    if _is_json_message(raw):
                        message = json.loads(raw)
                        header = message.get("header", {})
                        if header.get("tr_id") == "PINGPONG":
                            await ws.pong(raw)
                            continue
                        body = message.get("body", {})
                        output = body.get("output")
                        if on_key_iv is not None and isinstance(output, dict) and "key" in output:
                            on_key_iv(output["key"], output["iv"])
                        continue
                    await on_data_frame(raw)
        except (ConnectionClosed, OSError) as exc:
            logger.warning("KIS WS 연결 끊김: %s — %.1f초 후 재연결", exc, backoff)
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
