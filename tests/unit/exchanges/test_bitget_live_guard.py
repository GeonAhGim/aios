"""esc-1032-guard_flag 후속(task-1045) — BitgetTradingMixin의 place/cancel/
modify_order에 대한 @require_paper_sandbox 회귀 가드.

레드팀 #2026-09-02-32와 동일한 결함: Executor를 거치지 않고 거래소에
직결되는 확장 메서드는 `mode != "PAPER"` 하드 차단(Executor.execute())의
보호를 받지 못한다. 이 세 메서드는 demo_mode=False(LIVE로 구성된) adapter
에서 호출 자체가 FrozenZonePaperAdapterBlockedError로 막혀야 한다 — 다른
형제 믹스인(margin/grid/futures_trading 등, test_bitget_margin.py 참고)과
동일한 패턴.
"""
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_live_adapter() -> BitgetAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=False, http_client=client)


def _order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


async def test_place_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


async def test_cancel_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_order("order-1")


async def test_modify_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("order-1", price="100")
