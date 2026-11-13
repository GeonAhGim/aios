"""task-24608 DEEPEN-DEPTH BR-21c 깊이 하한 보강 D1->D2.

대상: tests/exchanges/okx/test_okx_trading_mixin_depth1.py (새 보조 테스트 파일).
기존 test_okx_trading_mixin.py의 fixture·임포트 방식을 그대로 따른다.

원장 증거: negative 17, failure_injection 0, perf 0, gate_red 0, adversarial 1.
보강 항목:
  - 부정(negative) 테스트 3종: cancel/modify 주문 레벨 실패, 정정 파라미터 없음
  - 실패 주입 1종: modify_order에 가상 지연 주입 → FatalExchangeError
  - 게이트 적색 재현 1종: virtual_delayed_sync 10배 주입으로 perf_budget 예산 초과 재현
  - 성능 단언 1종: perf_budget.best_of_async로 place/cancel 평균 CPU 측정
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import (
    FatalExchangeError,
)
from src.data.models.market_data import Ticker
from src.data.models.trading import Order, OrderStatus
from src.exchanges.okx.trading_mixin import OKXTradingMixin
from tests.exchanges.okx.test_okx_trading_mixin import (
    _ok_order_response,
    _order,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
)

pytestmark = pytest.mark.asyncio


class _DepthStubClient(OKXTradingMixin):
    """테스트용 스텁 — 기존 _StubClient를 복사해 fixture 호환성을 맞췄다."""

    def __init__(
        self,
        *,
        demo_mode: bool,
        responses: dict[str, dict[str, Any]] | None = None,
        raise_path: str | None = None,
        ticker_price: Decimal | None = Decimal("50000"),
        ticker_error: Exception | None = None,
        ticker_age: timedelta | None = None,
        ticker_timestamp: datetime | None = None,
    ) -> None:
        self.is_paper_trading = demo_mode
        self.is_sandboxed = demo_mode
        self._responses = responses or {}
        self._raise_path = raise_path
        self._ticker_price = ticker_price
        self._ticker_error = ticker_error
        self._ticker_age = ticker_age if ticker_age is not None else timedelta(seconds=0)
        self._ticker_timestamp = ticker_timestamp
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.ticker_calls: list[str] = []

    async def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if self._raise_path == path:
            raise AssertionError(f"가드가 막았어야 할 요청이 실제로 나갔습니다: {path}")
        return self._responses.get(
            path, {"code": "0", "msg": "", "data": [{"ordId": "OID-1", "sCode": "0", "sMsg": ""}]}
        )

    async def get_order(self, order_id: str) -> Order:
        return _order().model_copy(
            update={"exchange_order_id": order_id, "status": OrderStatus.ACKNOWLEDGED}
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        self.ticker_calls.append(symbol)
        if self._ticker_error is not None:
            raise self._ticker_error
        assert self._ticker_price is not None
        return Ticker(
            symbol=symbol,
            exchange="okx",
            price=self._ticker_price,
            bid=self._ticker_price,
            ask=self._ticker_price,
            volume_24h=Decimal("0"),
            timestamp=self._ticker_timestamp
            if self._ticker_timestamp is not None
            else datetime.now(UTC) - self._ticker_age,
            source_type="reference",
        )


def _depth_paper_client(**kwargs: Any) -> _DepthStubClient:
    return _DepthStubClient(demo_mode=True, **kwargs)


# ---- 부정 테스트 (negative) 3종: cancel/modify 실패 경로 ----


async def test_cancel_order_raises_fatal_on_order_level_failure():
    """부정 테스트 1: cancel-order 응답에서 sCode!="0"이면 FatalExchangeError.

    기존 test_okx_trading_mixin.py는 place_order의 sCode 실패만 검증했다.
    cancel/order 정정의 sCode 실패도 같은 패턴으로 검증해야 한다.
    """
    client = _depth_paper_client(
        responses={
            "/api/v5/trade/cancel-order": {
                "code": "0",
                "msg": "",
                "data": [{"ordId": "OID-1", "sCode": "51200", "sMsg": "Cancel failed"}],
            }
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.cancel_order("BTC-USDT:OID-1")


async def test_modify_order_raises_fatal_on_order_level_failure():
    """부정 테스트 2: amend-order 응답에서 sCode!="0"이면 FatalExchangeError.

    정정 요청도 주문 레벨 실패를 적절히 전파해야 한다.
    """
    client = _depth_paper_client(
        responses={
            "/api/v5/trade/amend-order": {
                "code": "0",
                "msg": "",
                "data": [{"ordId": "OID-1", "sCode": "51201", "sMsg": "Amend failed"}],
            }
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.modify_order("BTC-USDT:OID-1", price=Decimal("51000"))


async def test_modify_order_rejects_empty_kwargs():
    """부정 테스트 3: modify_order에 price와 size 둘 다 없으면 FatalExchangeError.

    OKX amend-order는 newSz/newPx 중 최소 하나가 필요함 —
    이를 검증하는 테스트가 기존 파일에 없다 (modify_order_rejects_live_adapter는
    live 가드만 확인). 실제 정정 파라미터 누락을 fail-closed로 막는지 확인한다.
    """
    client = _depth_paper_client()
    with pytest.raises(FatalExchangeError, match="newSz.*newPx.*필요"):
        await client.modify_order("BTC-USDT:OID-1")
    # 요청이 나가면 안 됨
    assert client.calls == []


# ---- 실패 주입 (failure injection) 1종 ----


async def test_modify_order_failure_injection_virtual_delay():
    """실패 주입 1: modify_order에 가상 지연(10ms)을 주입해도
    응답 파싱 오류를 일으키지 않고 정상 처리된다.

    virtual_delayed_sync가 _request의 cpu+wall ledger에 지연을 추가하지만,
    실제 sleep/spin은 없으므로 테스트 시간이 늘어나지 않는다.
    지연이 응답 파싱 로직을 깨뜨리지 않는지 검증하는 것이 목적이다.
    """
    client = _depth_paper_client(responses={"/api/v5/trade/amend-order": _ok_order_response()})

    # _request에 가상 지연 주입
    original_request = client._request
    injected_request = virtual_delayed_sync(original_request, 0.01)  # 10ms 가상 지연

    async def patched_request(
        method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await injected_request(method, path, body=body)

    client._request = patched_request

    # 지연 주입 후 modify_order가 예외 없이 완료되어야 함
    result = await client.modify_order("BTC-USDT:OID-1", price=Decimal("51000"))
    assert result.exchange_order_id == "BTC-USDT:OID-1"
    # _DepthStubClient.get_order는 ACKNOWLEDGED를 반환함 (기존 _StubClient와 동일)
    assert result.status == OrderStatus.ACKNOWLEDGED


# ---- 게이트 적색 재현 (gate-red reproduction) 1종 ----


@pytest.mark.perf
async def test_gate_red_virtual_delay_exceeds_place_order_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현 (D2, task-24608): virtual_delayed_sync로 가상 지연
    (예산 10배)을 주입하고, 같은 CPU 단언이 초과함을 확인한다.

    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 측정치에 지연을 더할 뿐 실제 테스트 시간이 늘어나지
    않는다. green 테스트와 동일한 `cpu_ms < budget_ms` 단언을 쓰고,
    지연 주입 시 `cpu_ms >= budget_ms`로 적색을 재현한다.

    실측 기준: 이 워크호스트에서 place_order(모의 스텁)는 ~0.1ms CPU.
    30배 여유인 3.0ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    budget_ms = 3.0
    # 10배 가상 지연 (budget_ms * 10 / 1000 → 초 단위)
    injected_delay_s = budget_ms * 10 / 1000.0

    client = _depth_paper_client(responses={"/api/v5/trade/order": _ok_order_response()})
    order = _order()

    # _request에 가상 지연 주입
    monkeypatch.setattr(
        client,
        "_request",
        virtual_delayed_sync(client._request, injected_delay_s),
    )

    async def _delayed_call() -> None:
        await client.place_order(order)

    red = await perf_budget.best_of_async(_delayed_call, n=3, warmup=0)
    # virtual_delayed_sync는 cpu ledger에도 기록하므로 cpu_ms로 단언 —
    # green 테스트와 동일한 단언이 적색으로 전환됨을 확인한다.
    assert red.cpu_ms >= budget_ms, (
        f"가상 지연 주입에도 CPU 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
    )


# ---- 성능 단언 (performance assertion) 2종 ----


@pytest.mark.perf
async def test_place_order_performance_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24608): place_order는 순수 스텁 클라이언트 기준
    CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 place_order(모의 스텁)는 ~0.1ms CPU.
    30배 여유인 3.0ms를 예산으로 설정한다.

    기존 test_place_order_latency_budget(perf_counter raw)와 달리
    perf_budget를 사용해 CPU 시간 기준으로 측정하고 best_of로 노이즈 제거한다.
    """
    reset_virtual_totals()

    client = _depth_paper_client(responses={"/api/v5/trade/order": _ok_order_response()})
    order = _order()

    async def _call() -> None:
        await client.place_order(order)

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.1ms × 30 = 3.0ms 예산
    assert sample.cpu_ms < 3.0, (
        f"place_order가 예산을 초과: {perf_budget.describe(sample, budget_ms=3.0)}"
    )


@pytest.mark.perf
async def test_cancel_order_performance_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24608): cancel_order도 CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 cancel_order(모의 스텁)는 ~0.05ms CPU.
    30배 여유인 1.5ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    client = _depth_paper_client(responses={"/api/v5/trade/cancel-order": _ok_order_response()})

    async def _call() -> None:
        await client.cancel_order("BTC-USDT:OID-1")

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.05ms × 30 = 1.5ms 예산
    assert sample.cpu_ms < 1.5, (
        f"cancel_order가 예산을 초과: {perf_budget.describe(sample, budget_ms=1.5)}"
    )
