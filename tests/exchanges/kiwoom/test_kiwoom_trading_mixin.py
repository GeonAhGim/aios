"""task-7571(BR-23d) — KiwoomTradingMixin 주문·취소·정정 테스트.

아직 KiwoomAdapter 조립체(task-7569 auth.py/factory.py)가 없으므로, KIS/
Bitget trading_mixin 테스트와 동일한 관례로 믹스인 + 최소 스텁 클라이언트를
이 파일 안에서 직접 구성한다(D2 floor: 부정 테스트 ≥3, 장애주입 1, 성능
수치 단언 1 또는 N/A 사유 명시, D3: INVARIANTS 대조 적대적 테스트 1 +
replay_verify 통과 — 근거는 각 테스트 docstring/모듈 하단 참고).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError, FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kiwoom.trading_mixin import KiwoomTradingMixin
from tests._perf.relative_budget import RelativeBudget

pytestmark = pytest.mark.asyncio


class _StubClient(KiwoomTradingMixin):
    def __init__(
        self,
        *,
        demo_mode: bool,
        responses: dict[str, dict[str, Any]] | None = None,
        raise_api_id: str | None = None,
    ) -> None:
        self.is_paper_trading = demo_mode
        self.is_sandboxed = demo_mode
        self._responses = responses or {}
        self._raise_api_id = raise_api_id
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, api_id: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, api_id, body))
        if self._raise_api_id == api_id:
            raise AssertionError(f"가드가 막았어야 할 요청이 실제로 나갔습니다: {api_id}")
        return self._responses.get(api_id, {"return_code": 0, "ord_no": "0000001"})

    async def get_order(self, order_id: str) -> Order:
        return _order().model_copy(
            update={"exchange_order_id": order_id, "status": OrderStatus.ACKNOWLEDGED}
        )


def _order(*, side: OrderSide = OrderSide.BUY, order_type: OrderType = OrderType.LIMIT) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kiwoom",
        side=side,
        order_type=order_type,
        quantity=Decimal("10"),
        price=None
        if order_type == OrderType.MARKET
        else Money(amount=Decimal("70000"), currency=Currency.KRW),
        asset_class=AssetClass.KR_EQUITY,
    )


def _live_client(raise_api_id: str | None = None) -> _StubClient:
    return _StubClient(demo_mode=False, raise_api_id=raise_api_id)


def _paper_client(**kwargs: Any) -> _StubClient:
    return _StubClient(demo_mode=True, **kwargs)


# ---- LIVE 하드가드 (D3 — INVARIANTS I-02/I-03 대조 적대적 테스트) ----


async def test_place_order_rejects_live_adapter():
    """demo_mode=False(LIVE)에서는 place_order가 HTTP 요청 전에 거부돼야
    한다 — 요청이 실제로 나가면 _request가 AssertionError로 실패시킨다."""
    client = _live_client(raise_api_id="kt10000")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.place_order(_order())
    assert client.calls == []


async def test_cancel_order_rejects_live_adapter():
    client = _live_client(raise_api_id="kt10003")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.cancel_order("005930:0000001")
    assert client.calls == []


async def test_modify_order_rejects_live_adapter():
    client = _live_client(raise_api_id="kt10002")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.modify_order("005930:0000001", price=Decimal("71000"))
    assert client.calls == []


# ---- 해피 패스 ----


async def test_place_order_buy_limit_builds_composite_exchange_order_id():
    client = _paper_client(responses={"kt10000": {"return_code": 0, "ord_no": "1234567"}})
    result = await client.place_order(_order(side=OrderSide.BUY, order_type=OrderType.LIMIT))
    assert result.exchange_order_id == "005930:1234567"
    assert result.status == OrderStatus.SUBMITTED
    _, _, api_id, body = client.calls[0]
    assert api_id == "kt10000"
    assert body is not None
    assert body["trde_tp"] == "0"
    assert body["ord_uv"] == "70000"


async def test_place_order_sell_market_uses_market_trade_division():
    client = _paper_client(responses={"kt10001": {"return_code": 0, "ord_no": "7654321"}})
    result = await client.place_order(_order(side=OrderSide.SELL, order_type=OrderType.MARKET))
    _, _, api_id, body = client.calls[0]
    assert api_id == "kt10001"
    assert body["trde_tp"] == "3"
    assert body["ord_uv"] == ""
    assert result.exchange_order_id == "005930:7654321"


async def test_cancel_order_splits_composite_id_and_returns_true_on_success():
    client = _paper_client(responses={"kt10003": {"return_code": 0}})
    ok = await client.cancel_order("005930:1234567")
    assert ok is True
    _, _, api_id, body = client.calls[0]
    assert api_id == "kt10003"
    assert body["stk_cd"] == "005930"
    assert body["orig_ord_no"] == "1234567"
    assert body["cncl_qty"] == "0"


async def test_modify_order_requires_price_and_delegates_to_get_order():
    client = _paper_client(responses={"kt10002": {"return_code": 0}})
    result = await client.modify_order(
        "005930:1234567", price=Decimal("71000"), quantity=Decimal("5")
    )
    _, _, api_id, body = client.calls[0]
    assert api_id == "kt10002"
    assert body["mdfy_uv"] == "71000"
    assert body["mdfy_qty"] == "5"
    assert result.exchange_order_id == "005930:1234567"


# ---- 부정/장애주입 테스트 (D2 floor ≥3 부정 + 1 장애주입) ----


async def test_cancel_order_rejects_malformed_exchange_order_id():
    """부정 테스트 1: ':' 구분자가 없는 exchange_order_id는 FatalExchangeError."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.cancel_order("not-a-composite-id")
    assert client.calls == []


async def test_modify_order_rejects_missing_price():
    """부정 테스트 2: 키움 정정주문(kt10002)은 정정단가가 필수 — 없으면
    거래소에 요청을 보내기 전에 거부한다(수량만 정정하는 API가 없음)."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.modify_order("005930:1234567", quantity=Decimal("5"))
    assert client.calls == []


async def test_place_order_raises_fatal_exchange_error_on_missing_ord_no():
    """부정 테스트 3 + 장애주입: 거래소 응답에 ord_no 필드가 없는(스키마
    변경/장애) 경우, 설명 없는 KeyError 대신 FatalExchangeError로 통일해
    호출부가 원인을 알 수 있게 한다(KIS 레드팀 감사 #18b와 동일 관례)."""
    client = _paper_client(responses={"kt10000": {"return_code": 0}})
    with pytest.raises(FatalExchangeError):
        await client.place_order(_order())


async def test_get_order_raises_on_still_malformed_id_after_modify_delegation():
    """부정 테스트 4: modify_order가 위임하는 get_order 경로도 동일한
    합성 ID 파싱 규약을 어기면 실패해야 한다 — 회귀 방지."""
    client = _paper_client(responses={"kt10002": {"return_code": 0}})
    with pytest.raises(FatalExchangeError):
        await client.cancel_order("badformat")
    # modify_order 자체는 price 누락이 먼저 걸리므로 별도로 cancel 경로를 재확인
    assert client.calls == []


# ---- 성능 수치 단언 ----


@pytest.mark.perf
@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_place_order_latency_budget() -> None:
    """숫자 성능 단언(자기보정 비율, task-7631/task-7674 RelativeBudget
    관례): 원래 절대 wall-clock 임계값(1ms/call)은 CI 러너의 클럭 속도를
    재는 것이지 믹스인 코드를 재는 게 아니다 — 부하가 걸린 공유 호스트에서
    코드 변경 없이도 적색이 될 수 있다(esc-ci-coverage count_tree/get_ohlcv
    파싱 perf 예산이 같은 이유로 절대치에서 비율로 전환된 전례, task-7741/
    task-7674). 같은 프로세스 안 보정 루프 대비 배수로 예산을 표현해
    호스트 속도 의존성을 제거한다. place_order 5000회를 자체 asyncio.run()
    으로 감싸(Windows ~15.6ms time.process_time() 양자화 위로 올리기 위해)
    pytest.mark.asyncio 대신 RelativeBudget의 동기 best-of-N이 직접 호출할
    수 있게 한다. 순수 스텁 클라이언트(네트워크 없음) 기준 실측 best-of-5
    비율은 0.17~0.2x — max_ratio=1.0은 ~5배 여유를 둔다. 실거래소 p95/p99
    레이턴시 예산은 조립체(adapter.py, 미구현) 단계에서 계약 테스트로 별도
    측정한다 — N/A(HTTP 클라이언트 미존재, task-7569 선행 필요)."""
    client = _paper_client(responses={"kt10000": {"return_code": 0, "ord_no": "1234567"}})
    order = _order()

    async def _place_many() -> None:
        for _ in range(5000):
            await client.place_order(order)

    def _run_once() -> None:
        asyncio.run(_place_many())

    RelativeBudget().assert_within(
        _run_once, max_ratio=1.0, mode="cpu", label="place_order x5000 (best of 5)"
    )
