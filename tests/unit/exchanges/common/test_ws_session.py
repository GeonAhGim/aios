"""L4-19 — `exchanges/common/ws_session.py::WsSession` 단위테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§8.1
(pong 미수신 재연결, ack 실패 코드 예외, seq 갭 → on_resync 1회, 재연결 후
재구독 순서, distrust 진입/해제 쌍). 실소켓 없이 가짜 connect_fn/sleep_fn을
주입해 결정론적으로 재현한다.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from src.core.observability.metrics_registry import MetricsRegistry
from src.exchanges.common.ws_session import (
    NOT_ACK,
    AckResult,
    HeartbeatSpec,
    WsAckError,
    WsProtocolError,
    WsSession,
)


class _Stop(Exception):
    """무한 재연결 루프를 테스트 안에서 끊기 위한 표식."""


class _FakeConnection:
    def __init__(
        self, messages: list[str], *, raise_after: BaseException | None = None,
        hang: bool = False, echo: tuple[str, str] | None = None,
    ) -> None:
        """`echo=(ping, pong)`이면 ping 수신 시 pong을 되돌려주는 살아있는 서버."""
        self._messages = messages
        self._raise_after = raise_after
        self._hang = hang
        self._echo = echo
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if self._echo is not None and message == self._echo[0]:
            self._queue.put_nowait(self._echo[1])

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._echo is not None:
            while True:
                yield await self._queue.get()
        if self._hang:
            await asyncio.Event().wait()
        if self._raise_after is not None:
            raise self._raise_after


class _Ctx:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _connect_sequence(connections: list[_FakeConnection]):
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] <= len(connections):
            return _Ctx(connections[calls["n"] - 1])
        raise _Stop

    return connect_fn, calls


def _ack(message: dict[str, Any]) -> AckResult:
    event = message.get("event")
    if event == "subscribe":
        return AckResult(is_ack=True, ok=True, detail="subscribe")
    if event == "error":
        return AckResult(is_ack=True, ok=False, detail=f"code={message.get('code')}")
    return NOT_ACK


def _seq(message: dict[str, Any]) -> int | None:
    value = message.get("seq")
    return int(value) if value is not None else None


async def _no_sleep(_: float) -> None:
    return None


async def _never(_: float) -> None:
    """하트비트를 시험하지 않는 테스트의 기본 ping sleep — 절대 깨어나지 않아
    ping 태스크가 수신 루프보다 먼저 miss를 판정하는 일이 없다."""
    await asyncio.Event().wait()


async def _yield(times: int = 3) -> None:
    for _ in range(times):  # 수신 태스크가 프레임을 처리할 기회를 준다
        await asyncio.sleep(0)


def _closed() -> ConnectionClosed:
    return ConnectionClosed(None, None)


SUB = {"op": "subscribe", "args": [{"channel": "ticker"}]}


class _Harness:
    """훅 호출 순서·핸들러 수신·메트릭을 한 곳에 모은 픽스처."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.received: list[dict[str, Any]] = []
        self.registry = MetricsRegistry()

    async def on_resync(self) -> None:
        self.events.append("resync")

    async def on_distrust(self, entered: bool) -> None:
        self.events.append(f"distrust:{entered}")

    async def handler(self, message: dict[str, Any]) -> None:
        self.received.append(message)

    def session(self, connect_fn, **kw: Any) -> WsSession:
        defaults: dict[str, Any] = dict(
            venue="test", channel="ticker", ack_validator=_ack, connect_fn=connect_fn,
            seq_extractor=_seq, on_resync=self.on_resync, on_distrust=self.on_distrust,
            sleep_fn=_no_sleep, ping_sleep_fn=_never, registry=self.registry,
        )
        defaults.update(kw)
        return WsSession("wss://fake", **defaults)

    def counter(self, name: str) -> float:
        labels = ("venue",) if "heartbeat" in name else ("venue", "channel")
        return sum(self.registry.counter(name, labels).samples().values())


# ---------- 하트비트(F11) ----------


async def test_heartbeat_pong_received_keeps_connection_alive():
    """ping마다 pong이 돌아오면 재연결하지 않는다 — pong 프레임은 핸들러에
    전달되지도 않는다."""
    h = _Harness()
    conn = _FakeConnection([], echo=("ping", "pong"))
    connect_fn, calls = _connect_sequence([conn])
    pings = {"n": 0}

    async def ping_sleep(_: float) -> None:
        pings["n"] += 1
        if pings["n"] > 3:
            raise _Stop
        await _yield()

    with pytest.raises(_Stop):
        await h.session(connect_fn, ping_sleep_fn=ping_sleep).run([SUB], h.handler)

    assert conn.sent == [json.dumps(SUB), "ping", "ping", "ping"]
    assert calls["n"] == 1
    assert h.received == []
    assert h.events == []
    assert h.counter("aios.exchange.ws.heartbeat_miss.count_total") == 0


async def test_heartbeat_missing_pong_reconnects_with_distrust():
    """ping 주기 안에 pong이 없으면 연결을 끊고 재연결(distrust 진입)."""
    h = _Harness()
    conn = _FakeConnection([], hang=True)  # 아무 응답도 없음
    connect_fn, calls = _connect_sequence([conn])
    sleeps: list[float] = []

    async def backoff_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(_Stop):
        await h.session(connect_fn, sleep_fn=backoff_sleep, ping_sleep_fn=_no_sleep).run(
            [SUB], h.handler
        )

    assert conn.sent == [json.dumps(SUB), "ping"]  # 2번째 ping 시점에 miss 판정
    assert calls["n"] == 2
    assert sleeps == [1.0]
    assert h.events == ["distrust:True"]
    assert h.counter("aios.exchange.ws.heartbeat_miss.count_total") == 1


async def test_custom_heartbeat_spec_is_honoured():
    h = _Harness()
    conn = _FakeConnection(["PONG-X"], echo=("PING-X", "PONG-X"))
    connect_fn, _ = _connect_sequence([conn])
    spec = HeartbeatSpec(interval_sec=5.0, ping_message="PING-X", pong_message="PONG-X")
    intervals: list[float] = []

    async def ping_sleep(seconds: float) -> None:
        intervals.append(seconds)
        if len(intervals) > 2:
            raise _Stop
        await _yield()

    with pytest.raises(_Stop):
        await h.session(connect_fn, heartbeat=spec, ping_sleep_fn=ping_sleep).run(
            [SUB], h.handler
        )

    assert intervals == [5.0, 5.0, 5.0]
    assert conn.sent[1:] == ["PING-X", "PING-X"]
    assert h.received == []  # 커스텀 pong도 핸들러로 가지 않는다


# ---------- ack 검증 ----------


async def test_ack_failure_code_raises_and_does_not_reconnect():
    """negative — 실패 코드 ack는 재연결로 삼키지 않고 `WsAckError`로 표면화."""
    h = _Harness()
    conn = _FakeConnection(['{"event":"error","code":"30001"}'], raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn, _FakeConnection([])])

    with pytest.raises(WsAckError, match="30001"):
        await h.session(connect_fn).run([SUB], h.handler)

    assert calls["n"] == 1
    assert h.received == []
    assert h.events == []


async def test_ack_success_is_not_forwarded_to_handler():
    h = _Harness()
    conn = _FakeConnection(
        ['{"event":"subscribe"}', '{"seq": 1, "data": "x"}'], raise_after=_closed()
    )
    connect_fn, _ = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert h.received == [{"seq": 1, "data": "x"}]


# ---------- seq 갭(F10) ----------


async def test_seq_gap_triggers_resync_once_and_keeps_delivering():
    h = _Harness()
    frames = [json.dumps({"seq": s}) for s in (1, 2, 4, 5, 5, 6)]  # 3 누락, 5 중복
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert [m["seq"] for m in h.received] == [1, 2, 4, 5, 5, 6]
    assert h.events == ["resync", "distrust:True"]  # 갭 1회 + 끊김 후 distrust
    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 1


async def test_contiguous_seq_does_not_resync():
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": s}) for s in (7, 8, 9)], raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert "resync" not in h.events
    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 0


async def test_seq_space_resets_on_reconnect():
    """새 연결의 첫 seq는 이전 연결의 마지막 seq와 비교하지 않는다 —
    재연결 자체가 이미 resync를 유발했으므로 갭 카운트는 0이어야 한다."""
    h = _Harness()
    first = _FakeConnection([json.dumps({"seq": 100})], raise_after=_closed())
    second = _FakeConnection([json.dumps({"seq": 1})], raise_after=_closed())
    connect_fn, _ = _connect_sequence([first, second])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 0
    assert h.events == ["distrust:True", "resync", "distrust:False", "distrust:True"]


# ---------- 재연결: 재로그인·재구독·resync·distrust 쌍 ----------


async def test_reconnect_resends_fresh_login_then_subscriptions_then_resyncs():
    h = _Harness()
    first = _FakeConnection([], raise_after=_closed())
    second = _FakeConnection([], raise_after=_closed())
    connect_fn, calls = _connect_sequence([first, second])
    logins = {"n": 0}

    def login_factory() -> list[dict[str, Any]]:
        logins["n"] += 1
        return [{"op": "login", "nonce": logins["n"]}]

    subs = [SUB, {"op": "subscribe", "args": [{"channel": "books"}]}]
    with pytest.raises(_Stop):
        await h.session(connect_fn, pre_messages_factory=login_factory).run(subs, h.handler)

    assert calls["n"] == 3
    assert first.sent == [json.dumps({"op": "login", "nonce": 1})] + [json.dumps(s) for s in subs]
    assert second.sent == [json.dumps({"op": "login", "nonce": 2})] + [json.dumps(s) for s in subs]
    # 진입/해제가 쌍을 이루고, resync는 재구독 뒤·해제 전.
    assert h.events == ["distrust:True", "resync", "distrust:False", "distrust:True"]
    assert h.counter("aios.exchange.ws.reconnect.count_total") == 1


async def test_first_connection_does_not_resync_or_distrust():
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1})], hang=True)
    connect_fn, _ = _connect_sequence([conn])

    async def ping_sleep(_: float) -> None:
        await _yield()
        raise _Stop

    with pytest.raises(_Stop):
        await h.session(connect_fn, ping_sleep_fn=ping_sleep).run([SUB], h.handler)

    assert h.events == []
    assert h.received == [{"seq": 1}]


async def test_backoff_doubles_to_cap_and_distrust_enters_once():
    """연속 연결 실패 중 distrust(True)는 한 번만 — 진입/해제 쌍 유지."""
    h = _Harness()
    attempts = {"n": 0}

    def connect_fn(url: str):
        attempts["n"] += 1
        if attempts["n"] <= 5:
            raise OSError("refused")
        raise _Stop

    sleeps: list[float] = []

    async def backoff_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(_Stop):
        await h.session(connect_fn, sleep_fn=backoff_sleep, max_backoff_seconds=8.0).run(
            [SUB], h.handler
        )

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert h.events == ["distrust:True"]


async def test_server_ending_stream_without_exception_is_treated_as_disconnect():
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1})])  # 예외 없이 이터레이션 종료
    connect_fn, calls = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert calls["n"] == 2
    assert h.events == ["distrust:True"]


# ---------- 파서 오류·핸들러 예외는 삼키지 않는다 ----------


@pytest.mark.parametrize("frame", ["not json", "[1, 2]", "42"])
async def test_non_dict_or_invalid_json_frame_raises_protocol_error(frame: str):
    """negative — 조용한 skip 금지: 프로토콜 위반은 예외로 표면화."""
    h = _Harness()
    conn = _FakeConnection([frame], raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn])

    with pytest.raises(WsProtocolError):
        await h.session(connect_fn).run([SUB], h.handler)

    assert calls["n"] == 1
    assert h.received == []


async def test_handler_exception_propagates_out_of_run():
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1})], raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    async def bad_handler(message: dict[str, Any]) -> None:
        raise RuntimeError("handler boom")

    with pytest.raises(RuntimeError, match="handler boom"):
        await h.session(connect_fn).run([SUB], bad_handler)


async def test_resync_failure_propagates_when_hook_raises():
    """세션은 resync 실패를 감추지 않는다 — 감출지 말지는 호출부(믹스인) 책임."""
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1}), json.dumps({"seq": 3})], raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    async def failing_resync() -> None:
        raise RuntimeError("rest down")

    with pytest.raises(RuntimeError, match="rest down"):
        await h.session(connect_fn, on_resync=failing_resync).run([SUB], h.handler)
