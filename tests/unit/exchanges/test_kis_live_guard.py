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


def _overseas_futureoption_order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="ESZ26",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.OVERSEAS_FUTURES,
        contract_multiplier=Decimal("50"),
        underlying_symbol="ES",
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


def _futureoption_order() -> Order:
    """task-1981(BR-6 리뷰 task-1977 REJECT 3번) — 이전까지 domestic_stock/
    bond만 behavioral negative 테스트가 있었고 선물옵션은 AST 정적검사
    (test_live_guard_coverage.py)만 커버했다. 정적검사는 데코레이터가
    "붙어 있는지"만 보므로, 데코레이터가 실제로 예외를 던지는지는 별도로
    단언해야 DoD(c)를 충족한다."""
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="101W09",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_FUTURES,
    )


async def test_place_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_futureoption_order(_futureoption_order())


async def test_cancel_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))


async def test_place_overseas_futureoption_order_rejects_live_adapter():
    """task-1983(BR-7 결함 수정, review task-1978 REJECT 후속) — 해외선물옵션도
    Executor를 거치지 않는 확장 메서드라 동일한 방어선이 필요하다.
    `_make_live_adapter()`의 handler는 요청이 실제로 나가면 AssertionError를
    던지므로, 이 테스트는 가드 예외뿐 아니라 httpx 요청이 0건임도 함께
    증명한다(AST 정적검사로는 대체할 수 없는 행위 검증)."""
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_overseas_futureoption_order(_overseas_futureoption_order())


async def test_cancel_overseas_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))
