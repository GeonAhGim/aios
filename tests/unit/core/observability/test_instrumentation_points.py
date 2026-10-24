"""PLT-10 도메인 계측 지점 5곳 — NullMetrics 스파이로 계측 호출을 단언한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-10.
대상: `order_service/submit.py`(+`gate.py`), `position_ledger.py`, `reconcile.py`,
`foundation/paper_control/application/submit_paper_intent.py`. 5곳 전부 기본값이
`NullMetrics`이므로(§9 decision) 여기서는 스파이를 명시적으로 주입해 호출을
관측한다 — 실제 DB/거래소 왕복 없이 repository 함수를 monkeypatch로 대역한다.

position_ledger / reconcile / submit_paper_intent 계측 테스트는
test_instrumentation_points_others.py로 분리(§7 파일 정책).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import (
    ORDER_SUBMIT_COUNT_TOTAL,
    ORDER_SUBMIT_DURATION_SECONDS,
    RISK_DECISION_COUNT_TOTAL,
    RISK_EVALUATION_DURATION_SECONDS,
)
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.order_service import repository as repository_module
from src.services.order_service.gate import GateDecision, GateOutcome, record_gate_decision
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@dataclass
class _SpyMetrics:
    """MetricsPort 스파이 — 호출을 그대로 기록만 한다(NullMetrics와 동일한
    무영향 계약이되, 관측을 위해 리스트에 남긴다)."""

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
    """`pool.acquire()`만 지원 — monkeypatch된 repository 함수는 conn을 쓰지 않는다."""

    def acquire(self) -> _NullConnCtx:
        return _NullConnCtx()


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


def _order(**overrides: object) -> Order:
    fields: dict[str, object] = dict(
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
    fields.update(overrides)
    return Order(**fields)


# ---- gate.py: aios.risk.decision.count_total / aios.risk.evaluation.duration_seconds ----


async def test_gate_records_decision_count_and_duration() -> None:
    spy = _SpyMetrics()
    decision = GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH",))

    record_gate_decision(spy, decision, duration_seconds=0.05)

    assert spy.counters == [
        (
            RISK_DECISION_COUNT_TOTAL,
            {"engine": "core", "effect": "DENY", "reason_code": "RISK_KILL_SWITCH"},
        )
    ]
    assert spy.observations == [(RISK_EVALUATION_DURATION_SECONDS, 0.05, {"engine": "core"})]


# ---- submit.py: aios.order.submit.count_total / .duration_seconds (+gate wiring) ----


async def test_submit_order_accepted_records_submit_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.core.observability.instrumentation_submit_accepted import submit_accepted_order

    result, spy = await submit_accepted_order(monkeypatch)

    assert result.status == OrderStatus.SUBMITTED
    # task-1715(P0-B) — pre_submit_gate가 필수 인자가 된 뒤로는 항상 평가되므로
    # gate 자체의 계측(RISK_DECISION_COUNT_TOTAL)도 함께 기록된다.
    assert spy.counters == [
        (RISK_DECISION_COUNT_TOTAL, {"engine": "core", "effect": "ALLOW", "reason_code": "none"}),
        (ORDER_SUBMIT_COUNT_TOTAL, {"exchange": "bitget", "mode": "paper", "outcome": "accepted"}),
    ]
    assert len(spy.observations) == 2
    assert spy.observations[0][0] == RISK_EVALUATION_DURATION_SECONDS
    assert spy.observations[1][0] == ORDER_SUBMIT_DURATION_SECONDS


async def test_submit_order_denied_records_gate_and_denied_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _order()
    adapter = FakeExchangeAdapter()
    spy = _SpyMetrics()

    async def deny_gate(context: object) -> GateDecision:
        return GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_GLOBAL",))

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order,
            user_id=uuid4(),
            adapter=adapter,
            pool=_FakePool(),
            pre_submit_gate=deny_gate,
            metrics=spy,
        )

    assert (
        RISK_DECISION_COUNT_TOTAL,
        {"engine": "core", "effect": "DENY", "reason_code": "RISK_KILL_SWITCH_GLOBAL"},
    ) in spy.counters
    assert (
        ORDER_SUBMIT_COUNT_TOTAL,
        {"exchange": "bitget", "mode": "paper", "outcome": "denied"},
    ) in spy.counters


# task-7636 DEEPEN — 실패주입: 저장소 의존성이 예외를 던지면(게이트 통과 후
# DB insert 장애) 아직 일어나지 않은 "제출 완료"를 계측이 거짓 보고해선
# 안 된다 — 게이트 계측만 남고 제출 결과 계측(count/duration)은 기록되지
# 않으며, 거래소 호출 자체가 이뤄지지 않았음을 함께 확인한다.
async def test_submit_order_insert_failure_records_only_gate_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = _order()
    adapter = FakeExchangeAdapter()
    spy = _SpyMetrics()

    async def failing_insert(conn: object, order: Order, *, user_id: object) -> Order:
        raise ConnectionError("simulated order repository failure")

    monkeypatch.setattr(repository_module, "insert", failing_insert)

    with pytest.raises(ConnectionError):
        await submit_order(
            order,
            user_id=uuid4(),
            adapter=adapter,
            pool=_FakePool(),
            metrics=spy,
            pre_submit_gate=_allow_gate,
        )

    assert spy.counters == [
        (RISK_DECISION_COUNT_TOTAL, {"engine": "core", "effect": "ALLOW", "reason_code": "none"})
    ]
    assert len(spy.observations) == 1
    assert spy.observations[0][0] == RISK_EVALUATION_DURATION_SECONDS
    assert adapter.place_order_call_count == 0


# 성능 예산(ADR-2026-09-09-C Decision 1) + 게이트 적색 재현은
# test_submit_order_perf_budget.py로 분리(§7 파일 정책 — 행동/계측 단언 축과
# 지연 측정 축은 독립적으로 변경되는 별개 관심사).
