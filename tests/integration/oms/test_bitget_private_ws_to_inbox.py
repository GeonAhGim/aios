"""L4-20 `bitget/private_ws_mixin.py` 통합테스트 — WS 이벤트 → inbox → FILLED 종단.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-20
("MockTransport 픽스처(문서 추정 표기)로 이벤트→FILLED 종단").

연결관리(하트비트·재연결)는 L4-19가 이미 `tests/unit/exchanges/common/
test_ws_session.py`로 검증했다 — 여기서는 그 위에 얹힌 새 파싱·배선
(`parse_private_order_row`/`subscribe_bitget_orders_to_inbox`)만 확인한다.
실소켓 대신 가짜 `connect_fn`(test_bitget_websocket.py와 동일 관례)을
주입하고, REST 호출이 전혀 없어야 함을 `httpx.MockTransport`가 예외를
던지는 핸들러로 못박는다(이 리프는 체결 이벤트만 다룬다 — resync는
범위 밖, 모듈 docstring 참조).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.private_ws_mixin import (
    parse_private_order_row,
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.conftest import create_test_user


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식."""


class _FakeConnection:
    def __init__(
        self, messages: list[str], *, raise_after: BaseException | None = None, hang: bool = False
    ) -> None:
        self._messages = messages
        self._raise_after = raise_after
        self._hang = hang
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._hang:
            await asyncio.Event().wait()
        if self._raise_after is not None:
            raise self._raise_after


class _TrackingConnectCtx:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self.exited = False

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        return False


async def _never(_: float) -> None:
    await asyncio.Event().wait()


def _no_rest_calls_adapter() -> BitgetAdapter:
    """이 리프는 resync를 구현하지 않는다 — REST 호출이 발생하면 즉시
    실패시켜, "이벤트→FILLED 종단"이 오직 WS 파싱·inbox 경로만으로
    이뤄짐을 못박는다(MockTransport 픽스처, DoD)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"예상치 못한 REST 호출: {request.url}")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        api_key="key123", api_secret="secret456", api_passphrase="phrase789", http_client=client
    )


async def _insert_order(
    pool, user_id: UUID, *, quantity: Decimal, exchange_order_id: str, client_order_id: str
) -> UUID:
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id, strategy_version,
                symbol, exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class
            ) VALUES ($1,$2,$3,'oms-ws-inbox-test','1.0.0','BTC/USDT','bitget','BUY','MARKET',
                      $4,'SUBMITTED',0,false,'CRYPTO')
            RETURNING order_id
            """,
            user_id,
            client_order_id,
            exchange_order_id,
            quantity,
        )
    return order_id


def _order_row(
    *,
    order_id: str,
    client_id: str,
    trade_id: str | None = None,
    fill_price: str = "100",
    fill_qty: str = "2",
) -> dict:
    # `trade_id`는 매 호출 고유값이 기본이다 — `provider_event_inbox`가
    # (venue, provider_event_id) UNIQUE라, TEST_DATABASE_URL은 테스트
    # 실행 간에 초기화되지 않으므로 고정 문자열을 재사용하면 이전 실행분과
    # 충돌해 "중복 전달"로 조용히 무시된다(test_inbox_processor.py의
    # `_insert_order` docstring과 동일 교훈).
    if trade_id is None:
        trade_id = f"trade-{uuid4().hex}"
    return {
        "instId": "BTCUSDT",
        "orderId": order_id,
        "clientOid": client_id,
        "side": "buy",
        "status": "filled",
        "tradeId": trade_id,
        "fillPrice": fill_price,
        "baseVolume": fill_qty,
        "fillSize": fill_qty,
        "priceAvg": fill_price,
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.1"}],
        "uTime": "1700000000000",
        "cTime": "1700000000000",
    }


def _envelope(rows: list[dict]) -> str:
    return json.dumps(
        {"action": "update", "arg": {"instType": "SPOT", "channel": "orders"}, "data": rows}
    )


async def test_fill_event_reaches_inbox_and_marks_order_filled_idempotently(pool):
    """DoD(a) — 체결 이벤트 1건이 inbox를 거쳐 FILLED로 전이하고 포지션
    (여기서는 fills/orders 행)이 정확히 1회만 반영된다. 같은 이벤트를
    2회(별도 WS 메시지로) 주입해도 fills 행 수가 늘지 않음을 수치로
    단언한다(멱등, I-03)."""
    user_id = await create_test_user(pool)
    exchange_order_id = f"ex-{uuid4().hex}"
    client_order_id = f"cid-{uuid4().hex}"
    order_id = await _insert_order(
        pool,
        user_id,
        quantity=Decimal("2"),
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
    )
    row = _order_row(order_id=exchange_order_id, client_id=client_order_id)
    message = _envelope([row])
    connection = _FakeConnection([message, message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "wss://ws.bitget.com/v2/ws/private"
            return _TrackingConnectCtx(connection)
        raise _StopTest

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == Decimal("2")
    assert fills_count == 1  # 중복 전달 2회 → fills 여전히 1행(멱등)


async def test_event_missing_order_identifiers_is_not_silently_dropped(pool):
    """DoD(b)/1 — 주문 ID(orderId/clientOid)가 전혀 없는 이벤트도 조용히
    버리지 않는다: 드롭 카운트가 아니라 inbox 행(IGNORED) 생성을
    단언한다."""
    row = {
        "instId": "BTCUSDT",
        "status": "live",
        # orderId/clientOid/tradeId 전부 없음 — 체결 수량도 없음. nonce는
        # raw_hash(→provider_event_id)를 실행마다 고유하게 만들어, 초기화
        # 안 되는 TEST_DATABASE_URL에 남은 이전 실행분과 충돌하지 않게 한다.
        "_test_nonce": uuid4().hex,
    }
    expected_provider_event_id = parse_private_order_row(
        row, received_at=datetime.now(timezone.utc)
    ).provider_event_id
    message = _envelope([row])
    connection = _FakeConnection([message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            expected_provider_event_id,
        )
    assert state == "IGNORED"  # 드롭이 아니라 inbox 행 생성(fail-closed)


async def test_event_pointing_at_unknown_order_id_is_not_silently_dropped(pool):
    """DoD(b)/2 — 알 수 없는 주문 ID를 가리키는 이벤트도 inbox 행을
    남긴다(IGNORED, L4-15의 fail-closed 매칭 경로를 그대로 탄다)."""
    exchange_order_id = f"ex-does-not-exist-{uuid4().hex}"
    client_order_id = f"cid-does-not-exist-{uuid4().hex}"
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(order_id=exchange_order_id, client_id=client_order_id, trade_id=trade_id)
    message = _envelope([row])
    connection = _FakeConnection([message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE exchange_order_id = $1", exchange_order_id
        )
    assert state == "IGNORED"
    assert fills_count == 0


async def test_cancelling_subscription_closes_the_connection(pool):
    """DoD(d) — 종료 시 구독이 실제로 해제된다(누수 금지). 태스크를
    취소하면 `WsSession.run`의 `async with connect_fn(...)` 블록이
    빠져나가며 연결 컨텍스트매니저의 `__aexit__`가 호출돼야 한다."""
    connection = _FakeConnection([], hang=True)
    ctx = _TrackingConnectCtx(connection)

    def connect_fn(url: str):
        return ctx

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)
    task = asyncio.create_task(
        subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert not task.done()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ctx.exited is True  # 연결이 실제로 정리됐다(구독 해제, 누수 금지)
