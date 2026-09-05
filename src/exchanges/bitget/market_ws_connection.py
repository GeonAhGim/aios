"""02b_bitget_api_v2_full_spec_v1.md §6 / L4-19 — Bitget WebSocket 연결 조립.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-E, §9 L4-19

2026-09-02 리팩터링 — 연결관리(재연결·백오프)를 파싱과 분리해 `connect_fn`
주입으로 실소켓 없이 테스트 가능하게 했다. 2026-09-03 task-105(f3799ba)가
ping/pong·ack 처리·재연결 후 REST 재동기화를 여기에 넣었다.

2026-09-06 task-1551(L4-19) — 그 로직을 거래소 공통 `exchanges/common/
ws_session.py::WsSession`으로 끌어올렸다(재구현 아님). 이 모듈에는 Bitget
고유 조립만 남는다: 하트비트 규약(`BITGET_HEARTBEAT`), ack 분류
(`classify_bitget_ack`), venue/channel 라벨, 그리고 기존 호출부·테스트가 쓰는
`_run_ws_subscription()` 시그니처의 얇은 래퍼. 동작 변화(스펙 §2-D 규칙):
- pong 미수신 → 재연결(이전엔 ping만 보내고 pong을 감시하지 않았다).
- subscribe/login 실패·error 이벤트 → `WsAckError` 표면화(이전엔 경고 로그 후 계속).
- 로그에 raw payload를 남기지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.exchanges.bitget.ws_parsers import BITGET_HEARTBEAT, classify_bitget_ack
from src.exchanges.common.ws_session import (
    ConnectFn,
    HeartbeatSpec,
    MessageHandler,
    ResyncHook,
    SeqExtractor,
    WsConnection,
    WsSession,
    default_connect,
    send_periodic_pings,
)

__all__ = [
    "ConnectFn",
    "MessageHandler",
    "ReconnectHook",
    "WsConnection",
    "_connect",
    "_run_ws_subscription",
    "_send_periodic_pings",
]

logger = logging.getLogger(__name__)

ReconnectHook = Callable[[], Awaitable[None]]
_connect = default_connect


async def _send_periodic_pings(
    ws: WsConnection,
    interval: float,
    ping_sleep_fn: Callable[[float], Awaitable[None]],
) -> None:
    """task-105 호환 — pong 감시 없는 순수 ping 루프(`monitor=None`).
    `WsSession`은 monitor를 붙인 `send_periodic_pings`를 직접 쓴다."""
    await send_periodic_pings(
        ws, HeartbeatSpec(interval_sec=interval, ping_message=BITGET_HEARTBEAT.ping_message,
                          pong_message=BITGET_HEARTBEAT.pong_message), ping_sleep_fn,
    )


def _channel_label(subscribe_msg: dict[str, Any]) -> str:
    args = subscribe_msg.get("args") or []
    first = args[0] if args and isinstance(args[0], dict) else {}
    return str(first.get("channel", "unknown"))


async def _run_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_message: MessageHandler,
    *,
    pre_messages_factory: Callable[[], list[dict[str, Any]]] | None = None,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    on_resync: ResyncHook | None = None,
    seq_extractor: SeqExtractor | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ping_interval_seconds: float = BITGET_HEARTBEAT.interval_sec,
    ping_sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Bitget 채널 하나를 `WsSession`으로 돌린다.

    훅 매핑(§2.1 재연결 책임 원칙 — EventBus에 직접 결합하지 않고 콜백 주입):
    - `on_reconnecting` ← `on_distrust(True)`: 끊김 감지 시 1회(연속 실패 중 반복 없음).
    - `on_resync`: 재연결 성공 직후(재로그인·재구독 뒤)와 seq 갭 감지 시 — 호출부가
      REST 스냅샷(get_ticker/get_open_orders 등)을 콜백으로 흘려보내는 자리.
    - `on_reconnected` ← `on_distrust(False)`: `on_resync` 완료 뒤.
    `pre_messages_factory`(Private 로그인)는 매 연결마다 새로 호출된다.
    `seq_extractor`는 기본 None(`ws_parsers.extract_bitget_seq` docstring — 미검증 opt-in).
    """

    async def on_distrust(entered: bool) -> None:
        if entered and on_reconnecting is not None:
            await on_reconnecting()
        if not entered and on_reconnected is not None:
            await on_reconnected()

    session = WsSession(
        url,
        venue="bitget",
        channel=_channel_label(subscribe_msg),
        ack_validator=classify_bitget_ack,
        connect_fn=connect_fn,
        heartbeat=HeartbeatSpec(
            interval_sec=ping_interval_seconds,
            ping_message=BITGET_HEARTBEAT.ping_message,
            pong_message=BITGET_HEARTBEAT.pong_message,
        ),
        seq_extractor=seq_extractor,
        on_resync=on_resync,
        on_distrust=on_distrust,
        pre_messages_factory=pre_messages_factory,
        max_backoff_seconds=max_backoff_seconds,
        sleep_fn=sleep_fn,
        ping_sleep_fn=ping_sleep_fn,
    )
    await session.run([subscribe_msg], on_message)
