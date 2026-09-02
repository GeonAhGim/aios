"""02b_bitget_api_v2_full_spec_v1.md §6/§9-4 — _run_ws_subscription()의
연결관리/재연결/백오프 루프. 실제 소켓 대신 가짜 connect_fn/sleep_fn을
주입해 결정적으로 재현한다(market_data_mixin.py 리팩터링 목적 그 자체)."""
import json

import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.market_data_mixin import _run_ws_subscription


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식 예외."""


class _FakeConnection:
    def __init__(self, messages, *, raise_after=None):
        self._messages = messages
        self._raise_after = raise_after
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._raise_after is not None:
            raise self._raise_after


class _FakeConnectCtx:
    def __init__(self, connection: _FakeConnection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_run_ws_subscription_reconnects_after_connection_closed_with_backoff():
    first_connection = _FakeConnection(
        ['{"event":"subscribe"}'], raise_after=ConnectionClosed(None, None)
    )
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(first_connection)
        raise _StopTest

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    received: list[dict] = []

    async def on_message(message: dict) -> None:
        received.append(message)

    with pytest.raises(_StopTest):
        await _run_ws_subscription(
            "wss://fake",
            {"op": "subscribe", "args": []},
            on_message,
            connect_fn=connect_fn,
            sleep_fn=fake_sleep,
        )

    assert call_count["n"] == 2
    assert sleep_calls == [1.0]
    assert received == [{"event": "subscribe"}]
    assert first_connection.sent == ['{"op": "subscribe", "args": []}']


async def test_run_ws_subscription_calls_reconnect_hooks_only_after_first_attempt():
    first_connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    second_connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    connections = [first_connection, second_connection]
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] <= len(connections):
            return _FakeConnectCtx(connections[call_count["n"] - 1])
        raise _StopTest

    async def fake_sleep(seconds: float) -> None:
        return None

    reconnecting_calls = 0
    reconnected_calls = 0

    async def on_reconnecting() -> None:
        nonlocal reconnecting_calls
        reconnecting_calls += 1

    async def on_reconnected() -> None:
        nonlocal reconnected_calls
        reconnected_calls += 1

    async def on_message(message: dict) -> None:
        pass

    with pytest.raises(_StopTest):
        await _run_ws_subscription(
            "wss://fake",
            {"op": "subscribe", "args": []},
            on_message,
            connect_fn=connect_fn,
            sleep_fn=fake_sleep,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    # 최초 연결 시도(call 1)는 재연결이 아니므로 on_reconnecting이 불리지
    # 않는다 — 이후 시도(call 2, 3)부터만 불린다.
    assert reconnecting_calls == 2
    # on_reconnected는 backoff>1.0인 상태로 연결에 "성공"한 직후(메시지
    # 수신 여부와 무관하게) 한 번 불린다 — call 2가 그 경우다. call 1은
    # 최초 연결이라 backoff==1.0이라 불리지 않고, call 3은 애초에 연결에
    # 성공하지 못한다(StopTest).
    assert reconnected_calls == 1


async def test_subscribe_order_stream_sends_login_before_subscribe():
    connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "wss://ws.bitget.com/v2/ws/private"
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = BitgetAdapter(api_key="key123", api_secret="secret456", api_passphrase="phrase789")

    async def callback(order) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.subscribe_order_stream(callback, connect_fn=connect_fn)

    assert len(connection.sent) == 2
    login_msg = json.loads(connection.sent[0])
    subscribe_msg = json.loads(connection.sent[1])
    assert login_msg["op"] == "login"
    assert login_msg["args"][0]["apiKey"] == "key123"
    assert subscribe_msg == {
        "op": "subscribe",
        "args": [{"instType": "SPOT", "channel": "orders", "instId": "default"}],
    }


async def test_subscribe_positions_stream_sends_login_before_subscribe():
    connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = BitgetAdapter(api_key="key123", api_secret="secret456", api_passphrase="phrase789")

    async def callback(position) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.subscribe_positions_stream(callback, connect_fn=connect_fn)

    assert len(connection.sent) == 2
    login_msg = json.loads(connection.sent[0])
    subscribe_msg = json.loads(connection.sent[1])
    assert login_msg["op"] == "login"
    assert subscribe_msg == {
        "op": "subscribe",
        "args": [{"instType": "USDT-FUTURES", "channel": "positions", "instId": "default"}],
    }


async def test_reconnect_resends_login_with_fresh_timestamp_not_stale_one(monkeypatch):
    """레드팀 #2026-09-02-31 회귀 테스트 — 이전엔 최초 연결 시 만든 고정
    로그인 메시지를 재연결마다 그대로 재전송해, 재연결 시점엔 이미
    타임스탬프가 오래돼 서명이 무효할 수밖에 없었다. 지금은 매 연결
    시도마다 `_login()`이 새로 호출돼 그때그때 타임스탬프로 재서명한다
    — `time.time()`을 결정적으로 증가시켜(실제 벽시계 타이밍에 기대지
    않고) 두 로그인 메시지의 timestamp/sign이 실제로 달라지는지 확인."""
    fake_now = [1000.0]

    def fake_time() -> float:
        fake_now[0] += 1.0
        return fake_now[0]

    monkeypatch.setattr(
        "src.exchanges.bitget.market_data_mixin.time.time", fake_time
    )

    first_connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    second_connection = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    connections = [first_connection, second_connection]
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] <= len(connections):
            return _FakeConnectCtx(connections[call_count["n"] - 1])
        raise _StopTest

    adapter = BitgetAdapter(api_key="key123", api_secret="secret456", api_passphrase="phrase789")

    async def callback(order) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.subscribe_order_stream(callback, connect_fn=connect_fn)

    first_login = json.loads(first_connection.sent[0])
    second_login = json.loads(second_connection.sent[0])
    assert first_login["op"] == "login"
    assert second_login["op"] == "login"
    assert first_login["args"][0]["timestamp"] != second_login["args"][0]["timestamp"]
    assert first_login["args"][0]["sign"] != second_login["args"][0]["sign"]
