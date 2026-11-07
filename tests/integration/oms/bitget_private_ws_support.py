"""Shared fake transport and order setup for Bitget private WS integration tests."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx

from src.exchanges.bitget.adapter import BitgetAdapter


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


async def _instant(_: float) -> None:
    """재연결 백오프(`WsSession._sleep_fn`)를 즉시 통과시킨다 — 수치 성능
    단언 테스트에서 인위적인 1초 고정 지연이 이벤트당 처리비용 측정을
    가리지 않게 하는 용도(재연결 자체를 없애는 게 아니라, 그 대기시간만
    제거)."""
    return


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
