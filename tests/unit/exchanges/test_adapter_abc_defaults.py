"""L4-13(task-1519) — `ExchangeAdapter` ABC 확장 기본 구현 + Bitget 조회 확장.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(adapter.py 행), §9 L4-13

검증 축:
1. 기본 구현 5종은 **명시적** `UnsupportedCapabilityError`를 던진다 —
   `NotImplementedError`도, 무음 빈 결과(`[]`/`None`)도 아니다.
2. 새 메서드가 추상이 아니라서 기존 구현체(최소 스텁, 통합테스트 대역)가
   여전히 인스턴스화된다(하위호환).
3. Bitget은 MRO상 자기 구현으로 해석되고, `find_order_by_client_id`
   (orderInfo?clientOid=)와 `get_fills(since=)`(startTime ms)가
   MockTransport로 기대한 요청을 만든다. naive `since`는 요청 전에 거부.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from src.data.models.trading import OrderStatus
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.market_ws_private_mixin import BitgetMarketDataWsPrivateMixin
from src.exchanges.bitget.trading_mixin import BitgetTradingMixin, _row_to_order
from src.exchanges.bitget.trading_query_mixin import BitgetTradingQueryMixin
from src.exchanges.common.adapter import ExchangeAdapter, UnsupportedCapabilityError

# ---------------------------------------------------------------------------
# 1·2. ABC 기본 구현
# ---------------------------------------------------------------------------


class _MinimalAdapter(ExchangeAdapter):
    """L4-13 이전의 추상 메서드만 구현한 최소 구현체 — 새 메서드를 하나도
    override하지 않았는데도 인스턴스화돼야 한다(하위호환 증명)."""

    @property
    def is_paper_trading(self) -> bool:
        return True

    @property
    def is_sandboxed(self) -> bool:
        return True

    def get_capabilities(self) -> Any:  # pragma: no cover - 호출 안 함
        raise AssertionError

    async def get_ticker(self, symbol: str) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_ohlcv(  # pragma: no cover
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> Any:
        raise AssertionError

    async def subscribe_ticker_stream(self, symbol: str, callback: Any) -> None:  # pragma: no cover
        raise AssertionError

    async def get_balance(self, asset: str | None = None) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_positions(self, symbol: str | None = None) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_order(self, order_id: str) -> Any:  # pragma: no cover
        raise AssertionError

    async def place_order(self, order: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def cancel_order(self, order_id: str) -> bool:  # pragma: no cover
        raise AssertionError

    async def modify_order(self, order_id: str, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def health_check(self) -> bool:  # pragma: no cover
        raise AssertionError


def test_new_query_methods_are_not_abstract():
    """새 메서드를 추상으로 추가했다면 여기서 TypeError가 난다."""
    adapter = _MinimalAdapter()
    assert isinstance(adapter, ExchangeAdapter)
    assert not ExchangeAdapter.__abstractmethods__ & {
        "get_open_orders",
        "get_fills",
        "find_order_by_client_id",
        "venue_profile",
        "subscribe_order_stream",
    }


async def test_default_get_open_orders_raises_explicitly():
    with pytest.raises(UnsupportedCapabilityError) as info:
        await _MinimalAdapter().get_open_orders()
    assert info.value.capability == "get_open_orders"
    assert info.value.adapter == "_MinimalAdapter"


async def test_default_get_fills_raises_explicitly():
    with pytest.raises(UnsupportedCapabilityError) as info:
        await _MinimalAdapter().get_fills("BTC/USDT", since=datetime.now(timezone.utc))
    assert info.value.capability == "get_fills"


async def test_default_find_order_by_client_id_raises_not_none():
    """negative — `None`은 "거래소가 그 id를 모른다"는 사실 주장이라
    미지원 venue가 None을 돌려주면 UNKNOWN resolver가 RESOLVED_ABSENT로
    오판한다(§6 F5-a). 예외여야 한다."""
    with pytest.raises(UnsupportedCapabilityError) as info:
        await _MinimalAdapter().find_order_by_client_id("c-1")
    assert info.value.capability == "find_order_by_client_id"


def test_default_venue_profile_raises_explicitly():
    with pytest.raises(UnsupportedCapabilityError) as info:
        _MinimalAdapter().venue_profile()
    assert info.value.capability == "venue_profile"


async def test_default_subscribe_order_stream_raises_explicitly():
    async def _cb(order: Any) -> None:  # pragma: no cover
        raise AssertionError

    with pytest.raises(UnsupportedCapabilityError) as info:
        await _MinimalAdapter().subscribe_order_stream(_cb)
    assert info.value.capability == "subscribe_order_stream"


def test_unsupported_capability_is_not_a_not_implemented_error():
    """negative — `except NotImplementedError` 폴백(REST 폴링 등)에 잡혀
    "미구현"과 "venue 미지원"이 섞이면 안 된다."""
    assert not issubclass(UnsupportedCapabilityError, NotImplementedError)
    err = UnsupportedCapabilityError("get_fills", "X")
    assert "get_fills" in str(err)
    assert "X" in str(err)


# ---------------------------------------------------------------------------
# 3. Bitget — MRO + MockTransport
# ---------------------------------------------------------------------------


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(
        200, json={"code": "00000", "msg": "success", "requestTime": 1, "data": data}
    )


def _order_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "orderId": "999",
        "clientOid": "c-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "orderType": "limit",
        "size": "0.01",
        "status": "live",
        "priceAvg": "0",
        "price": "80000",
        "cTime": "1787851009318",
        "uTime": "1787851009318",
    }
    row.update(overrides)
    return row


def test_bitget_resolves_query_methods_to_its_own_mixins_not_abc_defaults():
    """ABC 기본 구현이 Bitget의 실제 구현을 가리면(MRO 역전) 조용히
    미지원 예외가 나므로, 해석 결과를 명시적으로 고정한다."""
    assert BitgetAdapter.get_open_orders is BitgetTradingQueryMixin.get_open_orders
    assert BitgetAdapter.get_fills is BitgetTradingQueryMixin.get_fills
    assert BitgetAdapter.find_order_by_client_id is BitgetTradingQueryMixin.find_order_by_client_id
    assert (
        BitgetAdapter.subscribe_order_stream
        is BitgetMarketDataWsPrivateMixin.subscribe_order_stream
    )
    for name in (
        "get_open_orders",
        "get_fills",
        "find_order_by_client_id",
        "subscribe_order_stream",
    ):
        assert getattr(BitgetAdapter, name) is not getattr(ExchangeAdapter, name)
    # 분할 후에도 자금이동 믹스인이 조회 믹스인을 상속(어댑터 베이스 무변경)
    assert issubclass(BitgetTradingMixin, BitgetTradingQueryMixin)


async def test_find_order_by_client_id_queries_order_info_by_client_oid():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok([_order_row(status="partially_filled", fillSize="0.004")])

    order = await _make_adapter(handler).find_order_by_client_id("c-1")

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v2/spot/trade/orderInfo"
    assert seen[0].url.params["clientOid"] == "c-1"
    assert "orderId" not in seen[0].url.params
    assert order is not None
    assert order.client_order_id == "c-1"
    assert order.exchange_order_id == "999"
    assert order.status == OrderStatus.PARTIALLY_FILLED


async def test_find_order_by_client_id_returns_none_on_empty_data():
    """빈 data = 거래소가 그 id를 모른다(§6 F5-a NOT_FOUND 증거). 통신
    오류와 구분되도록 None이며, 예외가 아니다."""
    adapter = _make_adapter(lambda request: _ok([]))
    assert await adapter.find_order_by_client_id("missing") is None


async def test_find_order_by_client_id_propagates_api_error():
    """negative — 비성공 code가 None(=없음)으로 위장되면 안 된다."""
    from src.core.exceptions import ExchangeAPIError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "40012", "msg": "sign error", "data": None})

    with pytest.raises(ExchangeAPIError):
        await _make_adapter(handler).find_order_by_client_id("c-1")


async def test_get_fills_since_sends_start_time_in_epoch_ms():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok([{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}])

    since = datetime(2026, 9, 5, 0, 0, 0, tzinfo=timezone.utc)
    fills = await _make_adapter(handler).get_fills("BTC/USDT", since=since)

    assert seen[0].url.path == "/api/v2/spot/trade/fills"
    assert seen[0].url.params["symbol"] == "BTCUSDT"
    assert seen[0].url.params["startTime"] == str(int(since.timestamp() * 1000))
    assert "orderId" not in seen[0].url.params
    assert fills == [{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}]


async def test_get_fills_without_since_omits_start_time():
    """기존 호출 형태(since 미지정)는 요청이 그대로다(하위호환)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok([])

    await _make_adapter(handler).get_fills(order_id="999")
    assert seen[0].url.params["orderId"] == "999"
    assert "startTime" not in seen[0].url.params


async def test_get_fills_rejects_naive_since_before_any_request():
    """negative(fail-closed) — naive datetime은 시간대 추측이라 요청 자체를
    보내지 않는다."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _ok([])

    with pytest.raises(ValueError):
        await _make_adapter(handler).get_fills(since=datetime(2026, 9, 5, 0, 0, 0))
    assert calls == 0


def test_status_map_new_and_init_marked_acknowledged_unknown_otherwise():
    """스펙 §2-B: "new"/"init" → ACKNOWLEDGED(미검증 표기). 미지 문자열은
    여전히 UNKNOWN(8.3 원칙)."""
    assert _row_to_order(_order_row(status="new")).status == OrderStatus.ACKNOWLEDGED
    assert _row_to_order(_order_row(status="init")).status == OrderStatus.ACKNOWLEDGED
    assert _row_to_order(_order_row(status="???")).status == OrderStatus.UNKNOWN
