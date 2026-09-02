"""02d_kis_api_full_spec_v1.md §6 통합테스트 — KIS WebSocket 연결관리.

실제 소켓 대신 가짜 connect_fn을 주입해 결정적으로 재현한다(Bitget WS
테스트와 동일 원칙, tests/integration/test_bitget_websocket.py 참조).
"""
import json

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.websocket_mixin import _PRICE_FIELDS

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}
APPROVAL_RESPONSE = {"approval_key": "appr-1"}


def _price_frame(**overrides: str) -> str:
    values = {name: "" for name in _PRICE_FIELDS}
    values.update(
        {
            "MKSC_SHRN_ISCD": "005930",
            "STCK_PRPR": "70000",
            "BIDP1": "69900",
            "ASKP1": "70100",
            "ACML_VOL": "12345",
        }
    )
    values.update(overrides)
    body = "^".join(values[name] for name in _PRICE_FIELDS)
    return f"0|H0STCNT0|001|{body}"


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식 예외."""


class _FakeConnection:
    def __init__(self, messages, *, raise_after=None):
        self._messages = messages
        self._raise_after = raise_after
        self.sent: list[str] = []
        self.ponged: list[bytes | str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def pong(self, data: bytes | str = b"") -> None:
        self.ponged.append(data)

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


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    if request.url.path == "/oauth2/Approval":
        return httpx.Response(200, json=APPROVAL_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


async def test_subscribe_ticker_stream_sends_subscribe_message_with_approval_key():
    connection = _FakeConnection([_price_frame()], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "ws://ops.koreainvestment.com:31000"  # 모의투자 URL
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(lambda request: _route(request, {}))

    received = []

    async def callback(ticker) -> None:
        received.append(ticker)

    with pytest.raises(_StopTest):
        await adapter.subscribe_ticker_stream("005930", callback, connect_fn=connect_fn)

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["header"]["approval_key"] == "appr-1"
    assert subscribe_msg["body"]["input"]["tr_id"] == "H0STCNT0"
    assert subscribe_msg["body"]["input"]["tr_key"] == "005930"
    assert len(received) == 1
    assert received[0].symbol == "005930"


async def test_subscribe_ticker_stream_responds_to_pingpong():
    ping_message = json.dumps({"header": {"tr_id": "PINGPONG"}})
    connection = _FakeConnection([ping_message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(lambda request: _route(request, {}))

    async def callback(ticker) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.subscribe_ticker_stream("005930", callback, connect_fn=connect_fn)

    assert connection.ponged == [ping_message]


async def test_subscribe_order_notification_stream_extracts_key_iv_from_ack():
    ack_message = json.dumps(
        {"header": {"tr_id": "H0STCNI9"}, "body": {"output": {"key": "k1", "iv": "iv1"}}}
    )
    connection = _FakeConnection([ack_message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(lambda request: _route(request, {}))

    async def callback(order) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.subscribe_order_notification_stream(callback, connect_fn=connect_fn)

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["body"]["input"]["tr_id"] == "H0STCNI9"  # 모의투자 tr_id
