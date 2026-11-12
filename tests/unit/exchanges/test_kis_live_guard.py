"""esc-1011-guard_flag 후속(task-1073) — KISTradingMixin의 place/cancel/
modify_order에 대한 @require_paper_sandbox 회귀 가드.

레드팀 #2026-09-02-32와 동일한 결함: Executor를 거치지 않고 거래소에
직결되는 확장 메서드는 `mode != "PAPER"` 하드 차단(Executor.execute())의
보호를 받지 못한다. 이 세 메서드는 is_paper_trading=False(LIVE로 구성된)
adapter에서 호출 자체가 FrozenZonePaperAdapterBlockedError로 막혀야 한다
— bitget(test_bitget_live_guard.py, task-1045)과 동일한 패턴.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
)


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


# ---- 성능 단언 + 게이트 적색 재현 (task-24410 D2 보강) ----


@pytest.mark.perf
async def test_place_order_guard_latency_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24410): live adapter에서 place_order 호출 시
    @require_paper_sandbox 가드가 FrozenZonePaperAdapterBlockedError를
    던지기까지의 wall 지연이 예산 내에 있어야 한다.

    실측 기준: 이 워크호스트에서 place_order(모의 transport)는 ~0.1ms wall.
    30배 여유인 3ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    live_adapter = _make_live_adapter()

    async def _call() -> None:
        with pytest.raises(FrozenZonePaperAdapterBlockedError):
            await live_adapter.place_order(_order())

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.1ms x 30 = 3ms 예산
    assert sample.wall_ms < 3.0, (
        f"place_order 가드 지연이 예산을 초과: {perf_budget.describe(sample, budget_ms=3.0)}"
    )


@pytest.mark.perf
async def test_gate_red_virtual_delay_exceeds_guard_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현 (D2, task-24410): virtual_delayed_async로 가상 지연
    (예산 10배)을 주입하고, 같은 wall 단언이 적색이 됨을 확인한다.

    실제 sleep/busy-wait 금지 — virtual_delayed_async가 virtual ledger에
    나노초만 더하므로 측정치에 지연을 더할 뿐 실제 테스트 시간이 늘어나지
    않는다. green 테스트와 동일한 `wall_ms < budget_ms` 단언을 쓰고,
    지연 주입 시 `wall_ms >= budget_ms`로 적색을 재현한다.
    """
    reset_virtual_totals()

    budget_ms = 1.5
    # 10배 가상 지연 (budget_ms * 10 / 1000 -> 초 단위)
    injected_delay_s = budget_ms * 10 / 1000.0

    live_adapter = _make_live_adapter()

    # place_order 내부의 비동기 호출에 가상 지연 주입
    # require_paper_sandbox 데코레이터가 어댑터 메서드를 감싸므로,
    # 어댑터의 실제 비동기 메서드를 지연시켜 측정치에 반영한다.
    monkeypatch.setattr(
        live_adapter.__class__,
        "place_order",
        virtual_delayed_async(live_adapter.__class__.place_order, injected_delay_s),
    )

    async def _delayed_call() -> None:
        with pytest.raises(FrozenZonePaperAdapterBlockedError):
            await live_adapter.place_order(_order())

    red = await perf_budget.best_of_async(_delayed_call, n=3, warmup=0)
    # virtual_delayed_async는 wall ledger에만 기록하므로 wall_ms로 단언
    # green 테스트와 동일한 단언이 적색으로 전환됨을 확인한다.
    assert red.wall_ms >= budget_ms, (
        f"가상 지연 주입에도 wall 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
    )


@pytest.mark.perf
async def test_place_order_guard_rejects_live_adapter_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative: place_order가 live adapter에서 예외를 던지지 않으면
    AssertionError로 실패한다 — 가드가 작동하지 않는 상황을 검증한다."""
    live_adapter = _make_live_adapter()

    # 가드가 실제로 동작하는지 확인: 예외가 던져지지 않으면 테스트 실패
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


@pytest.mark.perf
async def test_place_order_guard_with_invalid_symbol_negative() -> None:
    """negative: 유효하지 않은 symbol로 place_order 호출 시
    FrozenZonePaperAdapterBlockedError가 symbol 관련 예외보다 먼저
    던져져야 한다 (가드가 symbol 검증보다 먼저 동작)."""
    live_adapter = _make_live_adapter()
    bad_order = _order().model_copy(update={"symbol": "INVALID_SYMBOL_WITH_UNDERSCORE"})

    # 가드가 symbol 검증 전에 먼저 차단해야 함
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(bad_order)


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


async def test_generated_order_cash_method_rejects_live_adapter():
    """review:1971 REJECT 후속(task-1975) — BR-12 생성기가 만든 주문성
    메서드 19건 전부에 `@require_paper_sandbox`가 없었다(CTSC0008U류는
    is_paper_trading일 때만 V로 치환되고, STTN1101U류는 애초에 치환
    대상이 아니라 LIVE로 구성된 adapter에서 실주문이 나갈 수 있었다).
    `order_cash_vttc0011u`가 대표로 배선을 증명한다 — 나머지 18건은
    test_live_guard_coverage.py의 TR 메타데이터 스캐너가 구조적으로
    막는다."""
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.order_cash_vttc0011u()
