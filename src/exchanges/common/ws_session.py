"""L4-19 — 거래소 공통 WebSocket 세션(하트비트·ack 검증·seq 갭·재연결 재동기화).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §6 F10·F11, §9 L4-19

task-105(f3799ba)가 `bitget/market_ws_connection.py::_run_ws_subscription`에
넣은 ping/pong·ack 처리·재연결 후 REST 재동기화를 거래소 무관 세션으로
끌어올린 것(재구현 아님 — 그 루프는 이제 이 클래스의 얇은 래퍼다). 거래소별
차이(하트비트 문자열, ack 프레임 모양, seq 위치)는 전부 생성자 인자로 주입.

규칙(§2-D `WsSession` 계약):
- ping 주기 안에 직전 ping의 pong이 없으면 끊고 재연결(F11), `on_distrust(True)`.
- subscribe/login ack가 실패 코드면 `WsAckError`로 표면화(조용한 skip 금지).
- seq 갭(≥1 누락) 감지 시 `on_resync()`(REST 스냅샷) 후 계속(F10).
- 재연결 성공 시 재로그인(`pre_messages_factory` 재호출)·재구독·`on_resync()`·
  `on_distrust(False)` 순 — distrust 진입/해제는 항상 쌍.
- JSON 아닌 프레임·dict 아닌 프레임·핸들러 예외는 삼키지 않는다.
- 로그에 raw payload를 남기지 않는다(§7.3 금지 항목).

`sleep_fn`(재연결 백오프)과 `ping_sleep_fn`(하트비트 간격)은 고의로 분리 —
백오프 테스트가 주입하는 즉시-완료 sleep이 ping 루프를 오염시키지 않게(task-105).

미검증: Bitget이 평문 "ping"에 평문 "pong"으로 답한다는 것은 문서 근거만
(§10 U5) — `HeartbeatSpec`으로 파라미터화해 두었다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

from src.core.observability.metrics_registry import MetricsRegistry, get_registry

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
ResyncHook = Callable[[], Awaitable[None]]
DistrustHook = Callable[[bool], Awaitable[None]]
SleepFn = Callable[[float], Awaitable[None]]
SeqExtractor = Callable[[dict[str, Any]], int | None]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def default_connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """`websockets.asyncio.client.connect`의 좁은 타입 래퍼 — 테스트가
    주입하는 가짜 `connect_fn`과 같은 시그니처로 맞춘다."""
    return _default_connect(url)  # type: ignore[return-value]


@dataclass(frozen=True)
class HeartbeatSpec:
    interval_sec: float = 30.0
    ping_message: str = "ping"
    pong_message: str = "pong"


@dataclass(frozen=True)
class AckResult:
    """`ack_validator` 반환값. `is_ack=False`면 데이터 프레임(핸들러로 전달).
    `is_ack=True, ok=False`면 세션이 `WsAckError`를 던진다."""

    is_ack: bool
    ok: bool = True
    detail: str = ""


NOT_ACK = AckResult(is_ack=False)
DEFAULT_HEARTBEAT = HeartbeatSpec()
AckValidator = Callable[[dict[str, Any]], AckResult]


class WsSessionError(Exception):
    """세션이 스스로 복구하지 않고 호출부로 표면화하는 오류의 공통 부모."""


class WsAckError(WsSessionError):
    """subscribe/login ack가 실패 코드 — 재연결로 해결되지 않는다."""


class WsProtocolError(WsSessionError):
    """JSON이 아니거나 dict가 아닌 프레임 — 파서 오류는 조용히 건너뛰지 않는다."""


class HeartbeatMissed(Exception):
    """ping 주기 안에 pong 없음 — 세션 내부에서 재연결 사유로 처리된다."""


class _StreamEnded(Exception):
    """서버가 예외 없이 스트림을 닫음 — 재연결 사유."""


class HeartbeatMonitor:
    """ping 1개당 pong 1개. 다음 ping 시점에 직전 ping의 pong이 아직 없으면 miss."""

    def __init__(self) -> None:
        self.pong_pending = False

    def on_ping_sent(self) -> None:
        self.pong_pending = True

    def on_pong(self) -> None:
        self.pong_pending = False


async def send_periodic_pings(
    ws: WsConnection,
    spec: HeartbeatSpec,
    sleep_fn: SleepFn,
    *,
    monitor: HeartbeatMonitor | None = None,
) -> None:
    """`monitor`가 있으면 pong 미수신을 `HeartbeatMissed`로 올린다(F11).
    없으면 task-105의 순수 ping 루프와 동일(호환 경로)."""
    while True:
        await sleep_fn(spec.interval_sec)
        if monitor is not None and monitor.pong_pending:
            raise HeartbeatMissed(f"{spec.interval_sec:g}s 안에 pong 없음")
        await ws.send(spec.ping_message)
        if monitor is not None:
            monitor.on_ping_sent()


class WsSession:
    def __init__(
        self,
        url: str,
        *,
        venue: str,
        channel: str,
        ack_validator: AckValidator,
        connect_fn: ConnectFn = default_connect,
        heartbeat: HeartbeatSpec = DEFAULT_HEARTBEAT,
        seq_extractor: SeqExtractor | None = None,
        on_resync: ResyncHook | None = None,
        on_distrust: DistrustHook | None = None,
        pre_messages_factory: Callable[[], list[dict[str, Any]]] | None = None,
        max_backoff_seconds: float = 30.0,
        sleep_fn: SleepFn = asyncio.sleep,
        ping_sleep_fn: SleepFn = asyncio.sleep,
        registry: MetricsRegistry | None = None,
    ) -> None:
        self._url = url
        self._venue = venue
        self._channel = channel
        self._ack_validator = ack_validator
        self._connect_fn = connect_fn
        self._heartbeat = heartbeat
        self._seq_extractor = seq_extractor
        self._on_resync = on_resync
        self._on_distrust = on_distrust
        self._pre_messages_factory = pre_messages_factory
        self._max_backoff = max_backoff_seconds
        self._sleep_fn = sleep_fn
        self._ping_sleep_fn = ping_sleep_fn
        reg = registry if registry is not None else get_registry()
        labels = ("venue", "channel")
        self._reconnects = reg.counter("aios.exchange.ws.reconnect.count_total", labels)
        self._gaps = reg.counter("aios.exchange.ws.sequence_gap.count_total", labels)
        self._misses = reg.counter("aios.exchange.ws.heartbeat_miss.count_total", ("venue",))

    async def run(self, subscriptions: list[dict[str, Any]], handler: MessageHandler) -> None:
        """연결·(재)로그인·구독·수신을 반복한다. 끊김(`ConnectionClosed`/
        `OSError`/pong 미수신/스트림 종료)만 재연결 사유이고, 그 외
        (`WsAckError`·`WsProtocolError`·핸들러 예외·connect_fn의 다른 예외)는
        그대로 호출부로 나간다."""
        backoff = 1.0
        distrusted = False
        while True:
            try:
                async with self._connect_fn(self._url) as ws:
                    await self._open(ws, subscriptions)
                    if distrusted:
                        self._reconnects.inc(venue=self._venue, channel=self._channel)
                        await self._resync()
                        await self._set_distrust(False)
                        distrusted = False
                    backoff = 1.0
                    await self._pump(ws, handler)
            except (ConnectionClosed, OSError, HeartbeatMissed, _StreamEnded) as exc:
                if isinstance(exc, HeartbeatMissed):
                    self._misses.inc(venue=self._venue)
                logger.warning(
                    "WS 연결 끊김(venue=%s channel=%s): %s — %.1f초 후 재연결",
                    self._venue, self._channel, exc, backoff,
                )
                if not distrusted:
                    distrusted = True
                    await self._set_distrust(True)
                await self._sleep_fn(backoff)
                backoff = min(backoff * 2, self._max_backoff)

    async def _open(self, ws: WsConnection, subscriptions: list[dict[str, Any]]) -> None:
        """매 연결마다 pre-message(로그인)를 *새로* 만들어 먼저 보낸다 —
        서명 타임스탬프는 재연결 시점 기준이어야 한다(레드팀 #2026-09-02-31)."""
        if self._pre_messages_factory is not None:
            for message in self._pre_messages_factory():
                await ws.send(json.dumps(message))
        for subscription in subscriptions:
            await ws.send(json.dumps(subscription))

    async def _pump(self, ws: WsConnection, handler: MessageHandler) -> None:
        monitor = HeartbeatMonitor()
        ping_task = asyncio.ensure_future(
            send_periodic_pings(ws, self._heartbeat, self._ping_sleep_fn, monitor=monitor)
        )
        recv_task = asyncio.ensure_future(self._receive(ws, handler, monitor))
        try:
            done, _ = await asyncio.wait(
                {ping_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()  # 먼저 끝난 쪽의 예외를 그대로 전파
        finally:
            for task in (ping_task, recv_task):
                task.cancel()
            await asyncio.gather(ping_task, recv_task, return_exceptions=True)

    async def _receive(
        self, ws: WsConnection, handler: MessageHandler, monitor: HeartbeatMonitor
    ) -> None:
        last_seq: int | None = None
        async for raw in ws:
            if raw == self._heartbeat.pong_message:
                monitor.on_pong()
                continue
            message = self._decode(raw)
            ack = self._ack_validator(message)
            if ack.is_ack:
                if not ack.ok:
                    raise WsAckError(
                        f"WS ack 실패(venue={self._venue} channel={self._channel}): {ack.detail}"
                    )
                logger.info(
                    "WS ack 수신(venue=%s channel=%s): %s", self._venue, self._channel, ack.detail
                )
                continue
            seq = self._seq_extractor(message) if self._seq_extractor is not None else None
            if seq is not None:
                if last_seq is not None and seq > last_seq + 1:
                    self._gaps.inc(venue=self._venue, channel=self._channel)
                    logger.warning(
                        "WS seq 갭(venue=%s channel=%s): %d → %d — REST 재동기화",
                        self._venue, self._channel, last_seq, seq,
                    )
                    await self._resync()
                last_seq = seq if last_seq is None else max(last_seq, seq)
            await handler(message)
        raise _StreamEnded("서버가 스트림을 종료")

    def _decode(self, raw: object) -> dict[str, Any]:
        if not isinstance(raw, (str, bytes, bytearray)):
            raise WsProtocolError(
                f"JSON 아닌 프레임(venue={self._venue} channel={self._channel}, "
                f"type={type(raw).__name__})"
            )
        try:
            message = json.loads(raw)
        except ValueError as exc:
            raise WsProtocolError(
                f"JSON 아닌 프레임(venue={self._venue} channel={self._channel}, "
                f"type={type(raw).__name__})"
            ) from exc
        if not isinstance(message, dict):
            raise WsProtocolError(
                f"dict 아닌 프레임(venue={self._venue} channel={self._channel}, "
                f"type={type(message).__name__})"
            )
        return message

    async def _resync(self) -> None:
        if self._on_resync is not None:
            await self._on_resync()

    async def _set_distrust(self, entered: bool) -> None:
        if self._on_distrust is not None:
            await self._on_distrust(entered)
