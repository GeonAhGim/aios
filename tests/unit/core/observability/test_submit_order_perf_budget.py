"""PLT-10 — submit_order() 계측 배선의 성능 예산 단언 + 게이트 적색 재현.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-10.
test_instrumentation_points.py의 5개 계측 지점 단언과 관심사가 다르다(그쪽은
"호출됐는가", 이쪽은 "얼마나 걸리는가") — ADR-2026-09-10-C §7 파일 정책에 따라
독립 변경 축으로 분리한 파일이다.

예산: ADR-2026-09-09-C Decision 1 축별 성능 예산표 "주문 제출→ACK p95 50ms
(paper)" — metrics.counter()/observe() 계측 배선 자체가 이 예산을 갉아먹지
않는지 확인하고, 그 단언이 실제 회귀에는 적색이 되는지(상시-녹색 아님)까지
증명한다.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.order_service import repository as repository_module
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.submit import submit_order
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

_ORDER_SUBMIT_ACK_P95_BUDGET_SECONDS = 0.05


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)
    gauges: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.gauges.append((name, value, labels))


class _NullConnCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _NullConnCtx:
        return _NullConnCtx()


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


def _order() -> Order:
    return Order(
        client_order_id=f"c-{uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=1,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        status=OrderStatus.CREATED,
    )


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


async def _submit_order_accepted_once(
    monkeypatch: pytest.MonkeyPatch, *, place_order_delay_seconds: float = 0.0
) -> float:
    order = _order()

    async def fake_insert(conn: object, order: Order, *, user_id: object) -> Order:
        return order

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "insert", fake_insert)
    monkeypatch.setattr(repository_module, "update_from_exchange", fake_update_from_exchange)

    async def on_place_order(placed: Order) -> Order:
        if place_order_delay_seconds:
            await asyncio.sleep(place_order_delay_seconds)
        return placed.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    adapter = FakeExchangeAdapter(on_place_order=on_place_order)
    started = time.perf_counter()
    result = await submit_order(
        order,
        user_id=uuid4(),
        adapter=adapter,
        pool=_FakePool(),
        metrics=_SpyMetrics(),
        pre_submit_gate=_allow_gate,
    )
    elapsed = time.perf_counter() - started
    assert result.status == OrderStatus.SUBMITTED
    return elapsed


async def test_submit_order_accepted_p95_latency_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = [await _submit_order_accepted_once(monkeypatch) for _ in range(30)]

    assert _p95(samples) < _ORDER_SUBMIT_ACK_P95_BUDGET_SECONDS


async def test_submit_order_p95_budget_guard_fails_on_injected_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 위 단언이 상시-녹색이 아님을 증명 — place_order에 예산의 2배 지연을
    # 주입하면 같은 p95 단언이 실제로 적색(AssertionError)이 되어야 한다.
    samples = [
        await _submit_order_accepted_once(
            monkeypatch, place_order_delay_seconds=_ORDER_SUBMIT_ACK_P95_BUDGET_SECONDS * 2
        )
        for _ in range(3)
    ]

    with pytest.raises(AssertionError):
        assert _p95(samples) < _ORDER_SUBMIT_ACK_P95_BUDGET_SECONDS
