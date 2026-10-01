"""NHAdapter 통합 테스트 — 주문(place/cancel/modify/get_order).

RATCHET-split(task-10196)로 test_nh_adapter.py에서 분리했다. place/cancel/
modify_order는 @require_paper_sandbox 가드를 테스트 전용으로 우회해야 하므로
`_nh_adapter_helpers.py`의 `_unguarded_*` 헬퍼를 쓴다(이유는 그 모듈 주석
참조).
"""

import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from tests.integration._nh_adapter_helpers import (
    TOKEN_RESPONSE,
    _make_adapter,
    _order,
    _route,
    _success,
    _unguarded_cancel_order,
    _unguarded_modify_order,
    _unguarded_place_order,
)

# ---------- trading: place_order ----------


async def test_place_order_buy_uses_cash_buy_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashBuy"
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "01"  # 지정가
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler}))
    order = _order()

    result = await _unguarded_place_order(adapter, order)

    assert result.exchange_order_id == "005930:999"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_order_sell_uses_cash_sell_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashSell"
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashSell": handler})
    )
    order = _order(side=OrderSide.SELL)

    await _unguarded_place_order(adapter, order)


async def test_place_order_market_type_uses_market_division_code():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "05"  # 시장가
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler}))
    order = _order(order_type=OrderType.MARKET, price=None)

    await _unguarded_place_order(adapter, order)


async def test_place_order_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected": "1"}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler}))
    with pytest.raises(FatalExchangeError):
        await _unguarded_place_order(adapter, _order())


# ---------- trading: cancel_order ----------


async def test_cancel_order_sends_confirmed_cancel_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cancel"
        body = json.loads(request.content)
        assert body["org_mkt_orr_no"] == 999
        assert body["iem_cd"] == "005930"
        assert body["all_pat_dit_cd"] == "1"
        return httpx.Response(200, json=_success())

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cancel": handler}))
    assert await _unguarded_cancel_order(adapter, "005930:999") is True


async def test_cancel_order_returns_true_on_alternate_success_code():
    """회귀 테스트 — 이전 구현은 `rsp_cd == "00000"`만 성공으로 봐서
    "00166" 같은 다른 성공 코드에서도 취소를 실패(False)로 잘못 보고하는
    버그가 있었다(_request()가 이미 성공 판정을 끝냈으므로 여기 도달한
    것 자체가 성공)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "00166", "rsp_msg": "정상처리완료"})

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cancel": handler}))
    assert await _unguarded_cancel_order(adapter, "005930:999") is True


async def test_cancel_order_raises_retryable_on_business_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "이미 체결된 주문입니다"})

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cancel": handler}))
    with pytest.raises(RetryableExchangeError):
        await _unguarded_cancel_order(adapter, "005930:999")


async def test_cancel_order_raises_fatal_on_malformed_order_id():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_cancel_order(adapter, "no-separator")


async def test_cancel_order_raises_fatal_on_non_numeric_mkt_orr_no():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_cancel_order(adapter, "005930:not-a-number")


# ---------- trading: modify_order ----------


async def test_modify_order_sends_confirmed_modify_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/modify"
        body = json.loads(request.content)
        assert body["org_mkt_orr_no"] == 999
        assert body["iem_cd"] == "005930"
        assert body["cor_qty"] == "5"
        assert body["cor_pr"] == "71000"
        return httpx.Response(200, json=_success({"mkt_orr_no": 1000}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/modify": handler}))
    order = await _unguarded_modify_order(
        adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
    )

    assert order.exchange_order_id == "005930:1000"
    assert order.status == OrderStatus.ACKNOWLEDGED


async def test_modify_order_accepts_quantity_kwarg_for_backward_compat():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"mkt_orr_no": 1000}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/modify": handler}))
    order = await _unguarded_modify_order(
        adapter, "005930:999", price=Decimal("71000"), quantity=Decimal("5")
    )
    assert order.quantity == Decimal("5")


async def test_modify_order_raises_fatal_when_price_missing():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(adapter, "005930:999", size=Decimal("5"))


async def test_modify_order_raises_fatal_when_size_missing():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(adapter, "005930:999", price=Decimal("71000"))


async def test_modify_order_raises_retryable_on_business_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "실패"})

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/modify": handler}))
    with pytest.raises(RetryableExchangeError):
        await _unguarded_modify_order(
            adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
        )


async def test_modify_order_raises_fatal_when_response_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected": "1"}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/modify": handler}))
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(
            adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
        )


# ---------- trading: get_order (confirmed structurally blocked) ----------


async def test_get_order_raises_not_implemented():
    """02e 스펙 §0-1/§3 — 공식 openapi.json으로 확인한 구조적 불일치
    (dailyOrderExecution 응답에 mkt_orr_no가 없음, trading_mixin.py 모듈
    docstring 참조) 때문에 근거 있는 구현이 불가능함을 검증한다."""
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.get_order("005930:999")
