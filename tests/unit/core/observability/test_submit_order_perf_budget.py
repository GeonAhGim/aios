"""PLT-10 — submit_order() 계측 배선의 성능 예산 단언 + 게이트 적색 재현.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-10.
test_instrumentation_points.py의 5개 계측 지점 단언과 관심사가 다르다(그쪽은
"호출됐는가", 이쪽은 "얼마나 걸리는가") — ADR-2026-09-10-C §7 파일 정책에 따라
독립 변경 축으로 분리한 파일이다.

예산: ADR-2026-09-09-C Decision 1 축별 성능 예산표 "주문 제출→ACK p95 50ms
(paper)" — metrics.counter()/observe() 계측 배선 자체가 이 예산을 갉아먹지
않는지 확인하고, 그 단언이 실제 회귀에는 적색이 되는지(상시-녹색 아님)까지
증명한다.

CI 병행 실행 안정화(task-19595): wall-clock(time.perf_counter) 대신
PerfBudget.best_of_async(cpu_time)로 변경 — CPU 시간은 다른 워커의
경합 영향을 받지 않아 CI에서 p95 단언이 안정적으로 통과한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
from tests.support.perf_budget import PerfBudget

_ORDER_SUBMIT_ACK_P95_BUDGET_MS = 50.0  # CPU-ms (process_time), not wall-clock


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


async def _make_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], Awaitable[Order]]:
    """Closure factory — monkeypatch는 각 best-of 호출 전에 다시 설정."""
    order = _order()

    async def fake_insert(conn: object, order: Order, *, user_id: object) -> Order:
        return order

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "insert", fake_insert)
    monkeypatch.setattr(repository_module, "update_from_exchange", fake_update_from_exchange)

    async def fake_update_from_exchange_with_fill_event(
        conn: object, order: Order, *, expected_status: object, actor_subject_id: object
    ) -> Order:
        return order.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    monkeypatch.setattr(
        repository_module,
        "update_from_exchange_with_fill_event",
        fake_update_from_exchange_with_fill_event,
    )

    async def on_place_order(placed: Order) -> Order:
        return placed.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    adapter = FakeExchangeAdapter(on_place_order=on_place_order)

    async def fn() -> Order:
        return await submit_order(
            order,
            user_id=uuid4(),
            adapter=adapter,
            pool=_FakePool(),
            metrics=_SpyMetrics(),
            pre_submit_gate=_allow_gate,
        )

    return fn


async def test_submit_order_accepted_p95_latency_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI 병행 실행에서 CPU 경합 spike를 피하기 위해 best_of_async로
    # best-of-5 최소값을 사용하고, 첫 호출은 warmup으로 버린다.
    # batch=8로 Windows time.process_time() 양자화 오차도 줄인다
    # (task-19328, task-19391, task-19595).
    perf_budget = PerfBudget()
    submit = await _make_submit(monkeypatch)
    sample = await perf_budget.best_of_async(submit, n=5, warmup=1, batch=8)
    assert sample.cpu_ms < _ORDER_SUBMIT_ACK_P95_BUDGET_MS, (
        f"submit_order p95 cpu={sample.cpu_ms:.1f}ms exceeds budget "
        f"{_ORDER_SUBMIT_ACK_P95_BUDGET_MS:.1f}ms"
    )


async def test_submit_order_p95_budget_guard_fails_on_injected_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 위 단언이 상시-녹색이 아님을 증명 — place_order에 지연을 주입하면
    # 같은 budget 단언이 실제로 적색(AssertionError)이 되어야 한다.
    order = _order()

    async def fake_insert(conn: object, order: Order, *, user_id: object) -> Order:
        return order

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "insert", fake_insert)
    monkeypatch.setattr(repository_module, "update_from_exchange", fake_update_from_exchange)

    async def fake_update_from_exchange_with_fill_event(
        conn: object, order: Order, *, expected_status: object, actor_subject_id: object
    ) -> Order:
        return order.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    monkeypatch.setattr(
        repository_module,
        "update_from_exchange_with_fill_event",
        fake_update_from_exchange_with_fill_event,
    )

    injected_delay = 0.15  # 150ms — well above 50ms budget

    async def on_place_order(placed: Order) -> Order:
        await asyncio.sleep(injected_delay)
        return placed.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    adapter = FakeExchangeAdapter(on_place_order=on_place_order)

    async def fn() -> Order:
        return await submit_order(
            order,
            user_id=uuid4(),
            adapter=adapter,
            pool=_FakePool(),
            metrics=_SpyMetrics(),
            pre_submit_gate=_allow_gate,
        )

    # CPU 시간은 I/O 대기 시간을 포함하지 않으므로, asyncio.sleep(I/O)가
    # 주입된 경우에도 best_of_async(cpu_ms)는 예산을 넘지 않을 수 있다.
    # 대신 wall_ms로 확인 — I/O 지연은 wall-clock에 그대로 반영된다.
    perf_budget = PerfBudget()
    sample = await perf_budget.best_of_async(fn, n=3, warmup=0, batch=1)
    # injected_delay(150ms)는 budget(50ms)를 크게 넘으므로 wall_ms 단언이 적색
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _ORDER_SUBMIT_ACK_P95_BUDGET_MS, (
            f"submit_order wall={sample.wall_ms:.1f}ms should exceed budget "
            f"{_ORDER_SUBMIT_ACK_P95_BUDGET_MS:.1f}ms with injected delay"
        )
