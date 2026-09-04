"""esc-1011-guard_flag 후속(task-1073) — KISTradingMixin의 place/cancel/
modify_order에 대한 @require_paper_sandbox 회귀 가드.

레드팀 #2026-09-02-32와 동일한 결함: Executor를 거치지 않고 거래소에
직결되는 확장 메서드는 `mode != "PAPER"` 하드 차단(Executor.execute())의
보호를 받지 못한다. 이 세 메서드는 is_paper_trading=False(LIVE로 구성된)
adapter에서 호출 자체가 FrozenZonePaperAdapterBlockedError로 막혀야 한다
— bitget(test_bitget_live_guard.py, task-1045)과 동일한 패턴.
"""
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter


def _make_live_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=False, http_client=http_client
    )


def _order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )


async def test_place_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


async def test_cancel_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_order("0001:0002")


async def test_modify_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", quantity=Decimal("5"))


async def test_place_bond_order_rejects_live_adapter():
    """task-1356(esc-1082 후속) — KISDomesticBondMixin.place_bond_order는
    이전 리프에서 가드가 누락돼 있었다(레드팀 #2026-09-02-32와 동일 결함
    클래스, test_live_guard_coverage.py가 구조적으로 재발을 막는다)."""
    live_adapter = _make_live_adapter()
    bond_order = _order().model_copy(update={"symbol": "KR6255081C48"})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_bond_order(bond_order)
