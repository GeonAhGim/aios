"""task-1783 BR-5(ADR-2026-09-06-I D2) — 해외주식 실시간 웹소켓 단위테스트.

파싱은 실제 소켓 없이(순수 함수), 구독은 가짜 connect_fn으로(결정적)
검증한다 — tests/integration/test_kis_websocket.py와 동일 패턴.
"""
import asyncio
import json

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.websocket_mixin import (
    _OVERSEAS_ORDERBOOK_FIELDS,
    _OVERSEAS_PRICE_FIELDS,
    parse_realtime_overseas_orderbook_message,
    parse_realtime_overseas_price_message,
)

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}
APPROVAL_RESPONSE = {"approval_key": "appr-1"}


def _overseas_price_frame(**overrides: str) -> str:
    values = {name: "" for name in _OVERSEAS_PRICE_FIELDS}
    values.update(
        {
            "SYMB": "AAPL",
            "LAST": "150.25",
            "PBID": "150.20",
            "PASK": "150.30",
            "TVOL": "98765",
        }
    )
    values.update(overrides)
    body = "^".join(values[name] for name in _OVERSEAS_PRICE_FIELDS)
    return f"0|HDFSCNT0|001|{body}"


def _overseas_orderbook_frame(**overrides: str) -> str:
    values = {name: "" for name in _OVERSEAS_ORDERBOOK_FIELDS}
    values.update(
        {
            "symb": "AAPL",
            "pbid1": "150.20",
            "pask1": "150.30",
            "vbid1": "100",
            "vask1": "200",
        }
    )
    values.update(overrides)
    body = "^".join(values[name] for name in _OVERSEAS_ORDERBOOK_FIELDS)
    return f"0|HDFSASP0|001|{body}"


def test_parse_realtime_overseas_price_message_parses_frame():
    tickers = parse_realtime_overseas_price_message(_overseas_price_frame())

    assert len(tickers) == 1
    assert tickers[0].symbol == "AAPL"
    assert tickers[0].price.to_eng_string() == "150.25"
    assert tickers[0].bid.to_eng_string() == "150.20"
    assert tickers[0].ask.to_eng_string() == "150.30"
    assert tickers[0].volume_24h.to_eng_string() == "98765"


def test_parse_realtime_overseas_price_message_ignores_other_tr_id():
    frame = _overseas_price_frame().replace("HDFSCNT0", "HDFSASP0")
    assert parse_realtime_overseas_price_message(frame) == []


def test_parse_realtime_overseas_price_message_ignores_malformed_frame():
    assert parse_realtime_overseas_price_message("not-a-valid-frame") == []


def test_parse_realtime_overseas_orderbook_message_parses_frame():
    book = parse_realtime_overseas_orderbook_message(_overseas_orderbook_frame())

    assert book is not None
    assert book.symbol == "AAPL"
    assert book.bids[0].price.to_eng_string() == "150.20"
    assert book.bids[0].quantity.to_eng_string() == "100"
    assert book.asks[0].price.to_eng_string() == "150.30"
    assert book.asks[0].quantity.to_eng_string() == "200"


def test_parse_realtime_overseas_orderbook_message_ignores_other_tr_id():
    assert parse_realtime_overseas_orderbook_message("0|HDFSCNT0|001|x^y") is None


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식 예외."""


class _FakeConnection:
    def __init__(self, messages, *, raise_after=None, hold_open=False):
        self._messages = messages
        self._raise_after = raise_after
        self._hold_open = hold_open
        self._stop = asyncio.Event()
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def pong(self, data: bytes | str = b"") -> None:
        pass

    def stop(self) -> None:
        self._stop.set()

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._raise_after is not None:
            raise self._raise_after
        if self._hold_open:
            await self._stop.wait()


class _FakeConnectCtx:
    def __init__(self, connection: _FakeConnection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if request.url.path == "/oauth2/Approval":
            return httpx.Response(200, json=APPROVAL_RESPONSE)
        raise AssertionError(f"no route for {request.url.path}")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


async def test_subscribe_overseas_ticker_stream_sends_subscribe_message():
    connection = _FakeConnection(
        [_overseas_price_frame()], raise_after=ConnectionClosed(None, None)
    )
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter()
    received = []

    async def callback(ticker) -> None:
        received.append(ticker)

    try:
        await adapter.subscribe_overseas_ticker_stream("RBAQAAPL", callback, connect_fn=connect_fn)
    except _StopTest:
        pass
    else:
        raise AssertionError("_StopTest expected")

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["body"]["input"]["tr_id"] == "HDFSCNT0"
    assert subscribe_msg["body"]["input"]["tr_key"] == "RBAQAAPL"
    assert len(received) == 1
    assert received[0].symbol == "AAPL"


async def test_subscribe_overseas_orderbook_stream_resubscribes_after_disconnect():
    """"시퀀스 결손 시 재구독" — 연결이 끊기면(서버 측 갭 유발이든 네트워크
    단절이든) 기존 `_run_kis_ws_subscription` 재연결 루프가 재연결 시마다
    구독 메시지를 다시 보낸다. 두 번째 연결에서도 구독 메시지가 다시
    전송됨을 확인해 이 보증이 해외 스트림에도 그대로 적용됨을 증명한다."""
    first_connection = _FakeConnection(
        [_overseas_orderbook_frame()], raise_after=ConnectionClosed(None, None)
    )
    second_connection = _FakeConnection([], raise_after=_StopTest())
    connections = [first_connection, second_connection]
    call_count = {"n": 0}

    def connect_fn(url: str):
        n = call_count["n"]
        call_count["n"] += 1
        return _FakeConnectCtx(connections[n])

    adapter = _make_adapter()
    received = []

    async def callback(book) -> None:
        received.append(book)

    try:
        await adapter.subscribe_overseas_orderbook_stream(
            "RBAQAAPL", callback, connect_fn=connect_fn
        )
    except _StopTest:
        pass
    else:
        raise AssertionError("_StopTest expected")

    assert call_count["n"] == 2  # 최초 연결 + 연결 끊김 후 재연결 1회
    assert len(first_connection.sent) == 1
    assert len(second_connection.sent) == 1  # 재연결 시 구독 메시지 재전송("재구독")
    second_subscribe = json.loads(second_connection.sent[0])
    assert second_subscribe["body"]["input"]["tr_id"] == "HDFSASP0"
    assert len(received) == 1


async def test_subscribe_overseas_ticker_stream_stops_callbacks_after_unsubscribe():
    """"구독 해제 후 콜백 0건" — 호출부가 구독 태스크를 취소하면(코루틴
    기반 스트림의 통상적 해제 방식) 그 이후로는 콜백이 전혀 호출되지
    않는다(추가 프레임이 도착해도 코루틴 자체가 더 이상 실행되지 않음)."""
    connection = _FakeConnection([], hold_open=True)

    def connect_fn(url: str):
        return _FakeConnectCtx(connection)

    adapter = _make_adapter()
    received = []

    async def callback(ticker) -> None:
        received.append(ticker)

    task = asyncio.ensure_future(
        adapter.subscribe_overseas_ticker_stream("RBAQAAPL", callback, connect_fn=connect_fn)
    )
    for _ in range(100):  # 구독 태스크가 승인키 조회·연결·구독 메시지 전송까지 진행하게 양보
        if connection.sent:
            break
        await asyncio.sleep(0.01)
    assert len(connection.sent) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert received == []
