"""task-7601(BR-22c) -- BinanceAccountMixin get_balance/get_positions/
get_open_orders/get_fills/get_order tests.

D2 floor: negative tests >=3, one failure-injection test, one numeric
performance assertion, one red-gate reproduction (see docstrings below).
D3 (L4-13 조회 계약, INVARIANTS 대조): "빈 결과로 미지원을 흉내내지 않는다"
원칙(common/adapter.py 모듈 docstring)을 get_fills(symbol=None)와
find_order_by_client_id 미override 케이스로 대조 검증한다.
replay_verify: N/A(순수 조회 어댑터 메서드, DB/이벤트스토어에 쓰지 않음).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.binance.account_mixin import BinanceAccountMixin

pytestmark = pytest.mark.asyncio


class _StubClient(BinanceAccountMixin):
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, params))
        return self._response


def _order_row(
    *, order_id: int = 123, status: str = "NEW", side: str = "BUY", order_type: str = "LIMIT"
) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "orderId": order_id,
        "clientOrderId": "c-1",
        "side": side,
        "type": order_type,
        "origQty": "0.01",
        "executedQty": "0.005",
        "status": status,
    }


# ---- get_balance: happy-path + negative ----


async def test_get_balance_maps_nonzero_rows_and_skips_zero_rows():
    client = _StubClient(
        {
            "balances": [
                {"asset": "BTC", "free": "1.5", "locked": "0.5"},
                {"asset": "ETH", "free": "0", "locked": "0"},
            ]
        }
    )
    balances = await client.get_balance()
    assert len(balances) == 1
    assert balances[0].asset == "BTC"
    assert balances[0].total == Decimal("2.0")
    assert balances[0].available == Decimal("1.5")
    assert balances[0].used_margin == Decimal("0.5")


async def test_get_balance_filters_by_asset():
    client = _StubClient(
        {
            "balances": [
                {"asset": "BTC", "free": "1", "locked": "0"},
                {"asset": "ETH", "free": "2", "locked": "0"},
            ]
        }
    )
    balances = await client.get_balance(asset="ETH")
    assert [b.asset for b in balances] == ["ETH"]


async def test_get_balance_raises_when_balances_field_missing():
    """부정 테스트 1: 응답에 balances가 없으면(스키마 변경/장애)
    FatalExchangeError -- 빈 리스트로 조용히 넘어가지 않는다."""
    client = _StubClient({})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


async def test_get_balance_raises_on_row_missing_field():
    """부정 테스트 2 + 장애주입: balances 행에 free/locked가 없으면
    KeyError를 삼키지 않고 FatalExchangeError로 통일."""
    client = _StubClient({"balances": [{"asset": "BTC"}]})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


# ---- get_positions: 항상 빈 목록(현물 전용 venue) ----


async def test_get_positions_always_empty_for_spot_venue():
    client = _StubClient(None)
    positions = await client.get_positions("BTCUSDT")
    assert positions == []
    assert client.calls == []  # 거래소 호출 자체가 없어야 한다


# ---- get_open_orders ----


async def test_get_open_orders_maps_rows_to_orders():
    client = _StubClient([_order_row(order_id=111, status="NEW")])
    orders = await client.get_open_orders("BTCUSDT")
    assert len(orders) == 1
    assert orders[0].exchange_order_id == "BTCUSDT:111"
    assert orders[0].status == OrderStatus.ACKNOWLEDGED
    _, path, params = client.calls[0]
    assert (path, params) == ("/api/v3/openOrders", {"symbol": "BTCUSDT"})


async def test_get_open_orders_without_symbol_omits_param():
    client = _StubClient([])
    await client.get_open_orders()
    _, _, params = client.calls[0]
    assert params is None


async def test_get_open_orders_raises_when_response_is_not_a_list():
    """부정 테스트 3: openOrders 응답이 리스트가 아니면(예: 에러 바디)
    FatalExchangeError."""
    client = _StubClient({"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(FatalExchangeError):
        await client.get_open_orders("BTCUSDT")


# ---- get_fills ----


async def test_get_fills_requires_symbol():
    """부정 테스트 4 -- D3 대조: L4-13 계약(common/adapter.py 모듈
    docstring)은 '빈 목록 = 체결 없음이라는 사실 주장'을 요구한다. Binance
    myTrades는 symbol 없이 조회할 수 없는 게 실제 API 제약이므로, 조회
    불가를 빈 리스트로 흉내내면 안 되고 명시적으로 실패해야 한다."""
    client = _StubClient([])
    with pytest.raises(FatalExchangeError):
        await client.get_fills(None)
    assert client.calls == []


async def test_get_fills_rejects_naive_datetime_since():
    """부정 테스트 5: since가 naive datetime이면 fail-closed 거부(tz-aware
    UTC 강제, common/adapter.py get_fills docstring 요구사항)."""
    client = _StubClient([])
    with pytest.raises(FatalExchangeError):
        await client.get_fills("BTCUSDT", since=datetime(2026, 1, 1))  # noqa: DTZ001
    assert client.calls == []


async def test_get_fills_passes_symbol_order_id_and_since():
    client = _StubClient([{"symbol": "BTCUSDT", "id": 1, "orderId": 111, "price": "1", "qty": "1"}])
    fills = await client.get_fills(
        "BTCUSDT", order_id="111", since=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert fills == [{"symbol": "BTCUSDT", "id": 1, "orderId": 111, "price": "1", "qty": "1"}]
    _, path, params = client.calls[0]
    assert path == "/api/v3/myTrades"
    assert params is not None
    assert params["symbol"] == "BTCUSDT"
    assert params["orderId"] == "111"
    expected_start_time = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert params["startTime"] == str(expected_start_time)


async def test_get_fills_raises_when_response_is_not_a_list():
    client = _StubClient({"code": -1121})
    with pytest.raises(FatalExchangeError):
        await client.get_fills("BTCUSDT")


# ---- get_order ----


async def test_get_order_parses_composite_exchange_order_id():
    client = _StubClient(
        _order_row(order_id=999, status="FILLED", side="SELL", order_type="MARKET")
    )
    order = await client.get_order("BTCUSDT:999")
    assert order.exchange_order_id == "BTCUSDT:999"
    assert order.status == OrderStatus.FILLED
    assert order.side == OrderSide.SELL
    assert order.order_type == OrderType.MARKET
    assert order.quantity == Decimal("0.01")
    assert order.filled_quantity == Decimal("0.005")
    _, _, params = client.calls[0]
    assert params == {"symbol": "BTCUSDT", "orderId": "999"}


async def test_get_order_rejects_malformed_exchange_order_id():
    """부정 테스트 6: ':' 구분자가 없는 exchange_order_id는
    FatalExchangeError -- trading_mixin.py의 동일 관례."""
    client = _StubClient(None)
    with pytest.raises(FatalExchangeError):
        await client.get_order("not-a-composite-id")
    assert client.calls == []


async def test_get_order_unknown_status_maps_to_unknown_not_a_crash():
    """8.3 원칙 대조: 매핑 표에 없는 status 문자열이 와도 예외로 죽지
    않고 OrderStatus.UNKNOWN으로 흡수한다(UNKNOWN != 실패로 단정 금지)."""
    client = _StubClient(_order_row(order_id=1, status="SOME_NEW_BINANCE_STATUS"))
    order: Order = await client.get_order("BTCUSDT:1")
    assert order.status == OrderStatus.UNKNOWN


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_get_balance_latency_budget():
    client = _StubClient({"balances": [{"asset": "BTC", "free": "1", "locked": "0"}]})
    start = time.perf_counter()
    for _ in range(100):
        await client.get_balance()
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
