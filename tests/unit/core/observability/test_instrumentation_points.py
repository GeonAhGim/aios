"""PLT-10 도메인 계측 지점 5곳 — NullMetrics 스파이로 계측 호출을 단언한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-10.
대상: `order_service/submit.py`(+`gate.py`), `position_ledger.py`, `reconcile.py`,
`foundation/paper_control/application/submit_paper_intent.py`. 5곳 전부 기본값이
`NullMetrics`이므로(§9 decision) 여기서는 스파이를 명시적으로 주입해 호출을
관측한다 — 실제 DB/거래소 왕복 없이 repository 함수를 monkeypatch로 대역한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import (
    FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL,
    ORDER_FILL_COUNT_TOTAL,
    ORDER_SUBMIT_COUNT_TOTAL,
    ORDER_SUBMIT_DURATION_SECONDS,
    ORDER_UNKNOWN_STATE_GAUGE,
    RISK_DECISION_COUNT_TOTAL,
    RISK_EVALUATION_DURATION_SECONDS,
)
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.paper_control.adapters.fake_paper_adapter import FakePaperExecutionAdapter
from src.foundation.paper_control.application import (
    submit_paper_intent as submit_paper_intent_module,
)
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.risk_gate.contracts.v1 import GateKind, RiskEvaluationView, RiskOutcome
from src.services.order_service import repository as repository_module
from src.services.order_service.gate import GateDecision, GateOutcome, record_gate_decision
from src.services.order_service.position_ledger import record_fill_in_position_ledger
from src.services.order_service.reconcile import resolve_unknown
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


class _ExplodingPool:
    """`acquire()` 호출 즉시 실패 — 계측이 DB 접근보다 먼저 실행됨을 증명한다."""

    def acquire(self) -> _NullConnCtx:
        raise AssertionError("메트릭 기록 전에 DB에 접근했다")


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
    order = _order()

    async def fake_insert(conn: object, order: Order, *, user_id: object) -> Order:
        return order

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "insert", fake_insert)
    monkeypatch.setattr(repository_module, "update_from_exchange", fake_update_from_exchange)

    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.SUBMITTED)
    spy = _SpyMetrics()

    result = await submit_order(
        order, user_id=uuid4(), adapter=adapter, pool=_FakePool(), metrics=spy
    )

    assert result.status == OrderStatus.SUBMITTED
    assert spy.counters == [
        (ORDER_SUBMIT_COUNT_TOTAL, {"exchange": "bitget", "mode": "paper", "outcome": "accepted"})
    ]
    assert len(spy.observations) == 1
    assert spy.observations[0][0] == ORDER_SUBMIT_DURATION_SECONDS


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


# ---- position_ledger.py: aios.order.fill.count_total ----


async def test_position_ledger_records_fill_before_touching_pool() -> None:
    order = _order(status=OrderStatus.FILLED, execution_id=42)
    spy = _SpyMetrics()

    with pytest.raises(AssertionError):
        await record_fill_in_position_ledger(_ExplodingPool(), order, metrics=spy)

    assert spy.counters == [(ORDER_FILL_COUNT_TOTAL, {"exchange": "bitget", "status": "FILLED"})]


async def test_position_ledger_skips_metric_when_not_filled() -> None:
    order = _order(status=OrderStatus.SUBMITTED, execution_id=42)
    spy = _SpyMetrics()

    await record_fill_in_position_ledger(_ExplodingPool(), order, metrics=spy)

    assert spy.counters == []


# ---- reconcile.py: aios.order.unknown_state.gauge ----


async def test_reconcile_gauge_zero_when_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _order(status=OrderStatus.UNKNOWN, execution_id=1).model_copy(
        update={"exchange_order_id": "ex-1"}
    )

    async def fake_get_by_order_id(conn: object, order_id: object) -> Order:
        return pending

    async def fake_update_from_exchange(
        conn: object, order: Order, *, expected_status: object
    ) -> Order:
        return order

    monkeypatch.setattr(repository_module, "get_by_order_id", fake_get_by_order_id)
    monkeypatch.setattr(repository_module, "update_from_exchange", fake_update_from_exchange)

    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    spy = _SpyMetrics()

    result = await resolve_unknown(
        pending.order_id, adapter=adapter, pool=_FakePool(), metrics=spy
    )

    assert result.status == OrderStatus.FILLED
    assert (ORDER_UNKNOWN_STATE_GAUGE, 0.0, {"exchange": "bitget"}) in spy.gauges


async def test_reconcile_gauge_one_when_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _order(status=OrderStatus.UNKNOWN, execution_id=1).model_copy(
        update={"exchange_order_id": "ex-1"}
    )

    async def fake_get_by_order_id(conn: object, order_id: object) -> Order:
        return pending

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(repository_module, "get_by_order_id", fake_get_by_order_id)

    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.UNKNOWN)
    spy = _SpyMetrics()

    result = await resolve_unknown(
        pending.order_id, adapter=adapter, pool=_FakePool(), sleep=fake_sleep, metrics=spy
    )

    assert result.status == OrderStatus.UNKNOWN
    assert (ORDER_UNKNOWN_STATE_GAUGE, 1.0, {"exchange": "bitget"}) in spy.gauges


# ---- submit_paper_intent.py: aios.foundation_paper_control.order_intent.count_total ----


class _FakePaperControlRepository:
    def __init__(self, deployment: PaperDeployment) -> None:
        self._deployment = deployment

    async def get_deployment(self, deployment_id: object) -> PaperDeployment:
        return self._deployment

    async def insert_order_intent(self, intent: object) -> object:
        return intent


def _paper_deployment() -> PaperDeployment:
    return PaperDeployment(
        id=uuid4(),
        tenant_id=uuid4(),
        connection_id=None,
        package_ref="pkg-1",
        mandate_revision_id=uuid4(),
        provenance=AdapterProvenance(
            adapter_type="fake",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="sandbox",
            provider_sandbox_account_ref="ref-1",
        ),
        state=DeploymentState.RUNNING,
        fence_token=1,
    )


async def test_submit_paper_intent_records_order_intent_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = _paper_deployment()
    repo = _FakePaperControlRepository(deployment)
    adapter = FakePaperExecutionAdapter()
    spy = _SpyMetrics()

    async def fake_evaluate_risk_gate(*args: object, **kwargs: object) -> RiskEvaluationView:
        return RiskEvaluationView(
            id=uuid4(),
            gate_kind=GateKind.PRE_INTENT,
            outcome=RiskOutcome.ALLOW,
            reason_codes=[],
            obligations=[],
            rule_version="v1",
            evaluated_at=datetime.now(timezone.utc),
            expires_at=None,
        )

    monkeypatch.setattr(submit_paper_intent_module, "evaluate_risk_gate", fake_evaluate_risk_gate)

    intent = await submit_paper_intent_module.submit_paper_intent(
        repo,
        adapter,
        None,  # type: ignore[arg-type]  # evaluate_risk_gate가 monkeypatch돼 미사용
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        deployment_id=deployment.id,
        expected_fence_token=1,
        sequence=1,
        metrics=spy,
    )

    assert intent.state == "SUBMITTED"
    assert spy.counters == [(FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL, {"mode": "paper"})]
