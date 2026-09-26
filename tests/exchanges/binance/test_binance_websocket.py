"""task-7602(BR-22e) -- BinanceWebSocketMixin ticker/depth/user-data stream
parsing tests.

실 WS 연결 없이 fake fixture 딕셔너리로 파서 3종(정상 체결(ticker)/정상
호가(depth)/malformed)을 단위 테스트한다. `subscribe_ticker_stream`/
`subscribe_order_stream` 자체(연결·재연결)는 `ws_session.py`(L4-19, 이미
테스트된 공통 컴포넌트)에 위임돼 있으므로 여기서는 fake `connect_fn`으로
한 프레임을 흘려보내는 배선 테스트만 추가한다.

D2 floor: negative >=3(malformed 메시지, DoD 요구사항), 장애주입 1, 성능
수치 단언 1. replay_verify: N/A(순수 파서 + WS 배선, DB/이벤트스토어 쓰기
없음).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.exchanges.binance.websocket import (
    USER_DATA_STREAM_PATH,
    BinanceWebSocketMixin,
    parse_depth_message,
    parse_execution_report_message,
    parse_ticker_message,
)

# ---- fake WS 메시지 3종 (DoD #1) ----

_NORMAL_TICKER_MESSAGE = {
    "e": "24hrTicker",
    "E": 1700000000000,
    "s": "BTCUSDT",
    "c": "60000.50",
    "b": "60000.00",
    "a": "60001.00",
    "v": "1234.5",
}

_NORMAL_DEPTH_MESSAGE = {
    "lastUpdateId": 160,
    "bids": [["60000.0", "1.5"]],
    "asks": [["60001.0", "2.0"]],
}

_NORMAL_EXECUTION_REPORT_MESSAGE = {
    "e": "executionReport",
    "s": "BTCUSDT",
    "c": "client-1",
    "S": "BUY",
    "o": "LIMIT",
    "q": "0.01",
    "z": "0.005",
    "X": "PARTIALLY_FILLED",
    "i": 5551212,
}

# ticker 파서 전용 malformed 픽스처 -- executionReport 전용 malformed는
# `test_parse_execution_report_message_malformed_raises_on_missing_field`
# 에서 별도 검증한다(파서마다 요구 필드가 달라 공유 리스트로 묶으면
# `pytest.skip`이 필요해지고, 그 자체가 code-ratchets의 skip_xfail 카운트를
# 올린다).
_MALFORMED_TICKER_MESSAGES: list[Any] = [
    ["not", "a", "dict"],  # 프레임 자체가 dict가 아님
    {"s": "BTCUSDT"},  # ticker인데 필수 필드(c/b/a/v/E) 없음
]


# ---- 정상 파싱 (DoD #1) ----


def test_parse_ticker_message_normal():
    ticker = parse_ticker_message(_NORMAL_TICKER_MESSAGE)
    assert ticker.symbol == "BTCUSDT"
    assert ticker.price == Decimal("60000.50")
    assert ticker.bid == Decimal("60000.00")
    assert ticker.ask == Decimal("60001.00")
    assert ticker.volume_24h == Decimal("1234.5")


def test_parse_depth_message_normal():
    book = parse_depth_message(_NORMAL_DEPTH_MESSAGE, symbol="BTCUSDT")
    assert book.symbol == "BTCUSDT"
    assert book.bids[0].price == Decimal("60000.0")
    assert book.asks[0].quantity == Decimal("2.0")


def test_parse_execution_report_message_normal():
    order = parse_execution_report_message(_NORMAL_EXECUTION_REPORT_MESSAGE)
    assert order is not None
    assert order.exchange_order_id == "BTCUSDT:5551212"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.quantity == Decimal("0.01")
    assert order.filled_quantity == Decimal("0.005")


def test_parse_execution_report_message_ignores_other_event_types():
    """`None` = "주문 이벤트가 아님"이라는 사실(예: outboundAccountPosition)
    -- malformed와 구분되는 정상 경로."""
    assert parse_execution_report_message({"e": "outboundAccountPosition"}) is None


# ---- malformed -> 예외 (DoD #2, >=3건) ----


@pytest.mark.parametrize("raw", _MALFORMED_TICKER_MESSAGES)
def test_parse_ticker_message_malformed_raises(raw: Any):
    """부정 테스트 1(파라미터화 2건) -- 비-dict 프레임과 필수 필드 누락
    dict 프레임 모두 ticker 파서가 예외로 거부해야 한다."""
    with pytest.raises(FatalExchangeError):
        parse_ticker_message(raw)


def test_parse_depth_message_malformed_raises_on_missing_bids():
    """부정 테스트 2 + 장애주입: depth 프레임에 bids가 없으면(거래소
    응답 스키마 변경) FatalExchangeError."""
    with pytest.raises(FatalExchangeError):
        parse_depth_message({"asks": []}, symbol="BTCUSDT")


def test_parse_depth_message_malformed_raises_on_non_dict_frame():
    non_dict_frame: Any = ["not", "a", "dict"]
    with pytest.raises(FatalExchangeError):
        parse_depth_message(non_dict_frame, symbol="BTCUSDT")


def test_parse_execution_report_message_malformed_raises_on_missing_field():
    """부정 테스트 3: executionReport인데 필수 필드(side/orderId 등)가
    없으면 조용히 None을 반환하지 않고 명시적으로 실패한다 -- "주문
    이벤트가 아님"과 "주문 이벤트인데 깨짐"을 구분해야 한다."""
    with pytest.raises(FatalExchangeError):
        parse_execution_report_message({"e": "executionReport", "s": "BTCUSDT"})


def test_parse_execution_report_message_raises_on_non_dict_frame():
    non_dict_frame: Any = ["not", "a", "dict"]
    with pytest.raises(FatalExchangeError):
        parse_execution_report_message(non_dict_frame)


def test_parse_execution_report_message_raises_when_event_type_missing():
    """부정 테스트 4: `e` 필드 자체가 없으면(완전히 다른 프레임) None으로
    조용히 넘기지 않고 실패한다."""
    with pytest.raises(FatalExchangeError):
        parse_execution_report_message({"s": "BTCUSDT"})


# ---- subscribe_* 배선 (fake connect_fn, 실 WS 연결 없음) ----


class _FakeWsConnection:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> _FakeWsConnection:
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def _fake_connect_fn(messages: list[str]) -> Any:
    @asynccontextmanager
    async def _connect(_url: str):
        yield _FakeWsConnection(messages)

    return _connect


class _StubWsClient(BinanceWebSocketMixin):
    def __init__(self, *, is_paper_trading: bool = True, listen_key_response: Any = None) -> None:
        self.is_paper_trading = is_paper_trading
        self._listen_key_response = listen_key_response
        self.requested_paths: list[str] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self.requested_paths.append(path)
        return self._listen_key_response


# A clean fake stream end raises WsSession's internal `_StreamEnded`, which
# `run()` treats as a reconnect trigger (not a propagated error, module
# docstring's "reuses ws_session.py rather than reimplementing it") -- it
# loops forever redelivering the same fixture and sleeping real backoff
# between attempts. `asyncio.wait_for` bounds the test deterministically:
# the one frame is always processed well within the timeout, and the test
# only cares that it reached the callback before the (real) backoff sleep
# it gets cancelled inside.
_WIRING_TIMEOUT_SEC = 0.2


async def test_subscribe_ticker_stream_delivers_parsed_ticker_to_callback():
    client = _StubWsClient()
    received = []
    frame = json.dumps(_NORMAL_TICKER_MESSAGE)

    async def callback(ticker: Any) -> None:
        received.append(ticker)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            client.subscribe_ticker_stream(
                "BTCUSDT", callback, connect_fn=_fake_connect_fn([frame])
            ),
            timeout=_WIRING_TIMEOUT_SEC,
        )
    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"


async def test_subscribe_order_stream_fetches_listen_key_and_uses_it_in_url():
    client = _StubWsClient(listen_key_response={"listenKey": "the-listen-key"})
    frame = json.dumps(_NORMAL_EXECUTION_REPORT_MESSAGE)
    received = []

    async def callback(order: Any) -> None:
        received.append(order)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            client.subscribe_order_stream(callback, connect_fn=_fake_connect_fn([frame])),
            timeout=_WIRING_TIMEOUT_SEC,
        )
    assert client.requested_paths == [USER_DATA_STREAM_PATH]
    assert len(received) == 1
    assert received[0].exchange_order_id == "BTCUSDT:5551212"


async def test_get_listen_key_raises_when_response_missing_listen_key():
    """부정 테스트 5: userDataStream 응답에 listenKey가 없으면 명시적으로
    실패한다(빈 문자열/None으로 조용히 넘어가 잘못된 URL로 연결 시도하지
    않음)."""
    client = _StubWsClient(listen_key_response={})
    with pytest.raises(FatalExchangeError):
        await client._get_listen_key()


# ---- 성능 수치 단언 ----


@pytest.mark.perf
def test_parse_ticker_message_latency_budget():
    start = time.perf_counter()
    for _ in range(1000):
        parse_ticker_message(_NORMAL_TICKER_MESSAGE)
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.0005
