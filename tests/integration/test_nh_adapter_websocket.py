"""NHAdapter 통합 테스트 — WebSocket 구독(connect_and_subscribe/mc 채널).

실제 소켓 대신 가짜 connect_fn을 주입해 결정적으로 재현한다(KIS WS 테스트와
동일 원칙, tests/integration/test_kis_websocket.py 참조). WebSocket 연결/
구독/재연결 책임은 `nhplug/realtime.py` 공식 소스로, (task-2615, 2026-09-16
재조사) `x-realtime-channels`로 확인한 `mc` 채널 데이터 프레임 필드 스키마는
docs/exchanges/NH_GAPS.md §2 참조.

RATCHET-split(task-10196, ADR-2026-09-10-C LOC 규율)로 test_nh_adapter.py에서
분리했다. 공개 API(각 테스트 함수 이름)는 변경하지 않았다.
"""

import json
from decimal import Decimal

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.core.exceptions import FatalExchangeError
from tests.integration._nh_adapter_helpers import TOKEN_RESPONSE, _make_adapter


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


async def test_connect_and_subscribe_sends_confirmed_envelope():
    connection = _FakeConnection(
        [json.dumps({"header": {"tr_cd": "mc", "tr_key": "005930"}, "body": {"x": 1}})],
        raise_after=ConnectionClosed(None, None),
    )
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "wss://moapi.nhplug.com:17070/websocket"  # 모의투자 URL
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )

    received: list[str] = []

    async def on_raw_frame(raw: str) -> None:
        received.append(raw)

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe("mc", "005930", on_raw_frame, connect_fn=connect_fn)

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["header"]["token"] == "tok-1"
    assert subscribe_msg["header"]["tr_type"] == "1"
    assert subscribe_msg["body"]["tr_cd"] == "mc"
    assert subscribe_msg["body"]["tr_key"] == "005930"
    assert len(received) == 1


async def test_connect_and_subscribe_uses_domestic_url_for_live_account():
    def connect_fn(url: str):
        assert url == "wss://api.nhplug.com:7070/websocket"
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=False
    )

    async def on_raw_frame(raw: str) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe("mc", "005930", on_raw_frame, connect_fn=connect_fn)


async def test_connect_and_subscribe_uses_overseas_url_when_requested():
    def connect_fn(url: str):
        assert url == "wss://api.nhplug.com:7080/websocket"
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=False
    )

    async def on_raw_frame(raw: str) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe(
            "RC", "GIC123", on_raw_frame, is_domestic=False, connect_fn=connect_fn
        )


async def test_connect_and_subscribe_reconnects_after_disconnect():
    """연결이 끊기면(ConnectionClosed) 재연결을 시도한다(§2.1 재연결
    책임) — on_reconnecting/on_reconnected 훅이 두 번째 연결에서만
    호출되는지 확인한다."""
    first = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    second = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}
    hooks: list[str] = []

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(first)
        if call_count["n"] == 2:
            return _FakeConnectCtx(second)
        raise _StopTest

    async def on_reconnecting() -> None:
        hooks.append("reconnecting")

    async def on_reconnected() -> None:
        hooks.append("reconnected")

    async def on_raw_frame(raw: str) -> None:
        pass

    async def instant_sleep(_seconds: float) -> None:
        return None

    from src.exchanges.nh import websocket_mixin

    with pytest.raises(_StopTest):
        await websocket_mixin._run_nh_ws_subscription(
            "wss://moapi.nhplug.com:17070/websocket",
            {"header": {"token": "t", "tr_type": "1"}, "body": {"tr_cd": "mc", "tr_key": "x"}},
            on_raw_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
            sleep_fn=instant_sleep,
        )

    # 1차 연결(끊김) → 2차 연결(reconnecting→성공→reconnected, 끊김) →
    # 3차 시도 직전에 connect_fn이 _StopTest를 던짐(그 전에 reconnecting은
    # 이미 호출됨).
    assert hooks == ["reconnecting", "reconnected", "reconnecting"]
    assert call_count["n"] == 3


# ---------- subscribe_ticker_stream (task-2615 -- mc 채널 필드 스키마 확인) ----------


_MC_PUSH_EXAMPLE = {
    # 공식 openapi.json x-realtime-channels.channels[tr_cd=mc].push_example
    # 그대로(docs/exchanges/NH_GAPS.md §2-1) -- 값 자체는 문서 예시.
    "code": "005940",
    "time": "14:00:31",
    "sign": "2",
    "change": "2500",
    "price": "31750",
    "chrate": "8.55",
    "high": "32200",
    "low": "29450",
    "offer": "31750",
    "bid": "31700",
    "volume": "837624",
}


async def test_subscribe_ticker_stream_maps_mc_frame_to_ticker():
    connection = _FakeConnection(
        [json.dumps({"header": {"tr_cd": "mc", "tr_key": "005940"}, "body": _MC_PUSH_EXAMPLE})],
        raise_after=ConnectionClosed(None, None),
    )
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )

    tickers = []

    async def callback(ticker) -> None:
        tickers.append(ticker)

    with pytest.raises(_StopTest):
        await adapter.subscribe_ticker_stream("005940", callback, connect_fn=connect_fn)

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["body"]["tr_cd"] == "mc"
    assert subscribe_msg["body"]["tr_key"] == "005940"
    assert len(tickers) == 1
    ticker = tickers[0]
    assert ticker.symbol == "005940"
    assert ticker.price == Decimal("31750")
    assert ticker.bid == Decimal("31700")
    assert ticker.ask == Decimal("31750")
    assert ticker.volume_24h == Decimal("837624")


async def test_subscribe_ticker_stream_ignores_subscribe_ack_and_other_channels():
    ack = json.dumps({"header": {"token": "t", "tr_type": "1"}, "body": {"tr_cd": "mc"}})
    other_channel = json.dumps({"header": {"tr_cd": "mb", "tr_key": "005940"}, "body": {}})
    connection = _FakeConnection([ack, other_channel], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )

    tickers = []

    async def callback(ticker) -> None:
        tickers.append(ticker)

    with pytest.raises(_StopTest):
        await adapter.subscribe_ticker_stream("005940", callback, connect_fn=connect_fn)

    assert tickers == []


async def test_subscribe_ticker_stream_raises_fatal_on_incomplete_mc_frame():
    frame = json.dumps({"header": {"tr_cd": "mc", "tr_key": "005940"}, "body": {"code": "005940"}})
    connection = _FakeConnection([frame], raise_after=ConnectionClosed(None, None))

    def connect_fn(url: str):
        return _FakeConnectCtx(connection)

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )

    with pytest.raises(FatalExchangeError):
        await adapter.subscribe_ticker_stream("005940", lambda t: None, connect_fn=connect_fn)
