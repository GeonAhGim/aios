"""task-7572(BR-23) — KiwoomWebSocketMixin 실시간 체결/호가/주문체결 테스트.

아직 KiwoomAdapter 조립체(task-7569 auth.py/factory.py)가 없으므로,
`test_kiwoom_trading_mixin.py`와 동일 관례로 믹스인 + 최소 스텁 클라이언트를
이 파일 안에서 직접 구성한다(D2 floor: 부정 테스트 >=3, 장애주입 1, 성능
수치 단언 1). D3(INVARIANTS 대조 적대적 테스트 + replay_verify)는
N/A(이 leaf는 자금 이동/주문 실행 경로가 아니라 시세·개인 주문 이벤트를
수신만 하는 읽기 전용 스트림 — trading_mixin.py의 LIVE 하드가드 같은
안전 불변식이 적용될 대상이 없다).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from websockets.exceptions import ConnectionClosed

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide, OrderStatus
from src.exchanges.kiwoom.websocket import KiwoomWebSocketMixin
from src.exchanges.kiwoom.websocket_parsing import (
    parse_order_fill_message,
    parse_orderbook_message,
    parse_stock_trade_message,
)

pytestmark = pytest.mark.asyncio


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식 예외
    (tests/integration/test_kis_websocket.py와 동일 관례)."""


class _FakeConnection:
    def __init__(self, messages: list[str], *, raise_after: Exception | None = None) -> None:
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
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StubClient(KiwoomWebSocketMixin):
    def __init__(self, *, is_paper_trading: bool = True, token: str = "tok-1") -> None:  # noqa: S107
        self.is_paper_trading = is_paper_trading
        self._token = token

    async def _ensure_token(self) -> str:
        return self._token


def _login_ack(return_code: int = 0) -> str:
    return json.dumps({"trnm": "LOGIN", "return_code": return_code, "return_msg": "OK"})


def _ping() -> str:
    return json.dumps({"trnm": "PING", "id": "p-1"})


def _real(type_code: str, item: str, values: dict[str, str]) -> str:
    return json.dumps(
        {"trnm": "REAL", "data": [{"type": type_code, "item": item, "values": values}]}
    )


def _single_shot_connect_fn(connection: _FakeConnection):
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(connection)
        raise _StopTest

    return connect_fn


# ---- 해피 패스 ----


async def test_subscribe_ticker_stream_logs_in_then_registers_and_parses_0b():
    connection = _FakeConnection(
        [
            _login_ack(),
            _real("0B", "005930", {"10": "70000", "27": "70100", "28": "69900", "15": "12345"}),
        ],
        raise_after=ConnectionClosed(None, None),
    )
    client = _StubClient()
    received = []

    async def callback(ticker) -> None:
        received.append(ticker)

    with pytest.raises(_StopTest):
        await client.subscribe_ticker_stream(
            "005930", callback, connect_fn=_single_shot_connect_fn(connection)
        )

    login_msg = json.loads(connection.sent[0])
    assert login_msg == {"trnm": "LOGIN", "token": "tok-1"}
    reg_msg = json.loads(connection.sent[1])
    assert reg_msg == {
        "trnm": "REG",
        "grp_no": "1",
        "refresh": "1",
        "data": [{"item": ["005930"], "type": ["0B"]}],
    }
    assert len(received) == 1
    assert received[0].symbol == "005930"
    assert received[0].price == Decimal("70000")
    assert received[0].bid == Decimal("69900")
    assert received[0].ask == Decimal("70100")


async def test_subscribe_orderbook_stream_parses_0d_ten_levels():
    values = {}
    for i in range(1, 11):
        values[str(40 + i)] = str(70000 + i)  # ask price level i
        values[str(60 + i)] = str(10 * i)  # ask qty level i
        values[str(50 + i)] = str(69000 - i)  # bid price level i
        values[str(70 + i)] = str(20 * i)  # bid qty level i
    connection = _FakeConnection(
        [_login_ack(), _real("0D", "005930", values)],
        raise_after=ConnectionClosed(None, None),
    )
    client = _StubClient()
    received = []

    async def callback(book) -> None:
        received.append(book)

    with pytest.raises(_StopTest):
        await client.subscribe_orderbook_stream(
            "005930", callback, connect_fn=_single_shot_connect_fn(connection)
        )

    assert len(received) == 1
    book = received[0]
    assert len(book.bids) == 10
    assert len(book.asks) == 10
    assert book.asks[0].price == Decimal("70001")
    assert book.bids[0].price == Decimal("68999")


async def test_subscribe_order_stream_parses_00_as_filled():
    connection = _FakeConnection(
        [
            _login_ack(),
            _real(
                "00",
                "",
                {
                    "9203": "1234567",
                    "9001": "005930",
                    "907": "2",  # 매수
                    "913": "체결",
                    "900": "10",
                    "911": "10",
                    "901": "70000",
                    "910": "70000",
                },
            ),
        ],
        raise_after=ConnectionClosed(None, None),
    )
    client = _StubClient()
    received = []

    async def callback(order) -> None:
        received.append(order)

    with pytest.raises(_StopTest):
        await client.subscribe_order_stream(
            callback, connect_fn=_single_shot_connect_fn(connection)
        )

    reg_msg = json.loads(connection.sent[1])
    assert reg_msg["data"] == [{"item": [], "type": ["00"]}]
    assert len(received) == 1
    order = received[0]
    assert order.exchange_order_id == "1234567"
    assert order.symbol == "005930"
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("10")


async def test_ping_is_echoed_back_verbatim():
    ping_message = _ping()
    connection = _FakeConnection(
        [_login_ack(), ping_message], raise_after=ConnectionClosed(None, None)
    )
    client = _StubClient()

    async def callback(ticker) -> None:
        pass

    with pytest.raises(_StopTest):
        await client.subscribe_ticker_stream(
            "005930", callback, connect_fn=_single_shot_connect_fn(connection)
        )

    # sent[0] = LOGIN, sent[1] = REG, sent[2] = PING echo
    assert connection.sent[2] == ping_message


# ---- 장애주입 (재연결) ----


async def test_connection_closed_triggers_reconnect_with_backoff():
    """장애주입: 첫 연결이 ConnectionClosed로 끊기면 backoff 후 재연결을
    시도해야 한다 -- 두 번째 connect_fn 호출이 실제로 일어나는지로 검증."""
    connection = _FakeConnection([_login_ack()], raise_after=ConnectionClosed(None, None))
    client = _StubClient()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return _FakeConnectCtx(connection)
        raise _StopTest

    async def callback(ticker) -> None:
        pass

    with pytest.raises(_StopTest):
        await client.subscribe_ticker_stream(
            "005930",
            callback,
            connect_fn=connect_fn,
        )

    assert call_count["n"] == 3
    assert len(sleep_calls) == 0  # default sleep_fn not overridden here; loop still reconnects


# ---- 부정 테스트 (D2 floor >= 3) ----


async def test_login_failure_raises_fatal_exchange_error():
    """부정 테스트 1: return_code != 0인 LOGIN ack는 FatalExchangeError."""
    connection = _FakeConnection([_login_ack(return_code=9)])
    client = _StubClient()

    async def callback(ticker) -> None:
        pass

    with pytest.raises(FatalExchangeError):
        await client.subscribe_ticker_stream(
            "005930", callback, connect_fn=_single_shot_connect_fn(connection)
        )


async def test_parse_stock_trade_message_raises_on_missing_field():
    """부정 테스트 2: 0B 메시지에 현재가(10) 필드가 없으면 조용히 빈
    리스트가 아니라 FatalExchangeError -- 스키마 변경/장애를 숨기지 않는다."""
    message = json.loads(_real("0B", "005930", {"15": "100"}))
    with pytest.raises(FatalExchangeError):
        parse_stock_trade_message(message)


async def test_parse_orderbook_message_raises_on_missing_field():
    """부정 테스트 3: 0D 메시지의 호가 필드 중 하나라도 없으면
    FatalExchangeError."""
    message = json.loads(_real("0D", "005930", {"41": "70100"}))
    with pytest.raises(FatalExchangeError):
        parse_orderbook_message(message)


async def test_parse_order_fill_message_raises_on_missing_field():
    """부정 테스트 4: 00 메시지에 주문번호(9203) 등 필수 필드가 없으면
    FatalExchangeError."""
    message = json.loads(_real("00", "", {"9001": "005930"}))
    with pytest.raises(FatalExchangeError):
        parse_order_fill_message(message)


async def test_data_frame_before_login_ack_is_ignored():
    """부정 테스트 5: LOGIN ack 이전에 온 REAL 프레임은(프로토콜 위반 신호)
    콜백을 타지 않고 무시돼야 한다 -- 인증 전 데이터는 신뢰하지 않는다."""
    connection = _FakeConnection(
        [_real("0B", "005930", {"10": "70000", "27": "1", "28": "1", "15": "1"}), _login_ack()],
        raise_after=ConnectionClosed(None, None),
    )
    client = _StubClient()
    received = []

    async def callback(ticker) -> None:
        received.append(ticker)

    with pytest.raises(_StopTest):
        await client.subscribe_ticker_stream(
            "005930", callback, connect_fn=_single_shot_connect_fn(connection)
        )

    assert received == []


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_parse_stock_trade_message_latency_budget():
    """숫자 성능 단언: 순수 파싱 함수(네트워크 없음) 기준 1000회 호출
    평균 1ms 미만 -- 실거래소 왕복 시간이 아니라 파싱 오버헤드 회귀 가드
    (trading_mixin.py의 동일 관례)."""
    import time

    message = json.loads(
        _real("0B", "005930", {"10": "70000", "27": "70100", "28": "69900", "15": "1"})
    )
    start = time.perf_counter()
    for _ in range(1000):
        parse_stock_trade_message(message)
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.001
