"""PLT-10 계측 지점 — position_ledger / reconcile / submit_paper_intent 테스트.

PLT-10의 나머지 대상 모듈(position_ledger, reconcile, submit_paper_intent)에 대한
계측 호출 단언 테스트. 주요 헬퍼(_SpyMetrics 등)는 test_instrumentation_points.py에
공유된다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from src.core.observability.metric_names import (
    FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL,
    ORDER_FILL_COUNT_TOTAL,
    ORDER_UNKNOWN_STATE_GAUGE,
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
from src.services.order_service import reconcile as reconcile_module
from src.services.order_service import repository as repository_module
from src.services.order_service.position_ledger import record_fill_in_position_ledger
from src.services.order_service.reconcile import resolve_unknown
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

# ---- 공유 헬퍼 (test_instrumentation_points.py와 중복되나 분할 파일 정책상 복원) ----


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
#
# task-1604(L4-16) — `resolve_unknown`의 실제 판정은
# `oms.application.unknown_resolver.resolve_unknown`(실DB 트랜잭션)로
# 위임됐다(tests/integration/oms/test_unknown_resolver.py가 그 판정 로직
# 자체를 실DB로 증명). 이 두 테스트는 계측 지점(래퍼가 위임 결과를 보고
# 게이지를 올바르게 기록하는지)만 좁게 확인하므로, 위임 호출 자체를
# monkeypatch로 대역해 실DB 없이 유지한다(모듈 docstring "실제 DB/거래소
# 왕복 없이" 원칙 그대로).


async def test_reconcile_gauge_zero_when_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _order(status=OrderStatus.FILLED, execution_id=1).model_copy(
        update={"exchange_order_id": "ex-1"}
    )
    resolved_view = SimpleNamespace(status=OrderStatus.FILLED, exchange="bitget")

    async def fake_resolve_unknown(order_id: object, **kwargs: object) -> object:
        return resolved_view

    async def fake_get_by_order_id(conn: object, order_id: object) -> Order:
        return pending

    monkeypatch.setattr(reconcile_module.unknown_resolver, "resolve_unknown", fake_resolve_unknown)
    monkeypatch.setattr(repository_module, "get_by_order_id", fake_get_by_order_id)

    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    spy = _SpyMetrics()

    result = await resolve_unknown(pending.order_id, adapter=adapter, pool=_FakePool(), metrics=spy)

    assert result.status == OrderStatus.FILLED
    assert (ORDER_UNKNOWN_STATE_GAUGE, 0.0, {"exchange": "bitget"}) in spy.gauges


async def test_reconcile_gauge_one_when_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _order(status=OrderStatus.UNKNOWN, execution_id=1).model_copy(
        update={"exchange_order_id": "ex-1"}
    )
    unresolved_view = SimpleNamespace(status=OrderStatus.UNKNOWN, exchange="bitget")

    async def fake_resolve_unknown(order_id: object, **kwargs: object) -> object:
        return unresolved_view

    async def fake_get_by_order_id(conn: object, order_id: object) -> Order:
        return pending

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(reconcile_module.unknown_resolver, "resolve_unknown", fake_resolve_unknown)
    monkeypatch.setattr(repository_module, "get_by_order_id", fake_get_by_order_id)

    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.UNKNOWN)
    spy = _SpyMetrics()

    result = await resolve_unknown(
        pending.order_id, adapter=adapter, pool=_FakePool(), sleep=fake_sleep, metrics=spy
    )

    assert result.status == OrderStatus.UNKNOWN
    assert (ORDER_UNKNOWN_STATE_GAUGE, 1.0, {"exchange": "bitget"}) in spy.gauges


# task-7636 DEEPEN — negative: 위임된 판정은 끝났는데 persisted 행이 없는
# 상태는 불변식 위반이다 — RuntimeError로 명시적으로 거부한다(reconcile.py
# 하단 raise). 게이지는 그 검사보다 먼저 기록되므로(§7.2), 불변식 위반이
# 계측 자체를 가리지 않음도 함께 확인한다.
async def test_reconcile_raises_when_persisted_row_missing_after_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _order(status=OrderStatus.FILLED, execution_id=1).model_copy(
        update={"exchange_order_id": "ex-1"}
    )
    resolved_view = SimpleNamespace(status=OrderStatus.FILLED, exchange="bitget")

    async def fake_resolve_unknown(order_id: object, **kwargs: object) -> object:
        return resolved_view

    async def fake_get_by_order_id_missing(conn: object, order_id: object) -> Order | None:
        return None

    monkeypatch.setattr(reconcile_module.unknown_resolver, "resolve_unknown", fake_resolve_unknown)
    monkeypatch.setattr(repository_module, "get_by_order_id", fake_get_by_order_id_missing)

    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    spy = _SpyMetrics()

    with pytest.raises(RuntimeError, match="해소 처리 후 행이 없습니다"):
        await resolve_unknown(pending.order_id, adapter=adapter, pool=_FakePool(), metrics=spy)

    assert (ORDER_UNKNOWN_STATE_GAUGE, 0.0, {"exchange": "bitget"}) in spy.gauges


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
            evaluated_at=datetime.now(UTC),
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


# task-3147 DEEPEN — 도메인 거부 negative 2건 추가(기존 1건 + 아래 2건 = 3건).
# §7.2 카운터는 제출 완료된 의도만 세야 하므로, 거부 시 오탐 없이 비어야 한다.
# risk_repo/mandate_repo/connection_repo는 각 거부 경로 모두 평가 전에 실패하거나
# monkeypatch로 대역돼 미사용 — 셋 다 Any로 캐스팅해 넘긴다.
_UNUSED_PORTS: tuple[Any, Any, Any] = (None, None, None)


async def test_submit_paper_intent_denied_by_risk_gate_records_no_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = _paper_deployment()
    repo = _FakePaperControlRepository(deployment)
    adapter = FakePaperExecutionAdapter()
    spy = _SpyMetrics()

    async def deny_risk_gate(*args: object, **kwargs: object) -> RiskEvaluationView:
        return RiskEvaluationView(
            id=uuid4(),
            gate_kind=GateKind.PRE_INTENT,
            outcome=RiskOutcome.DENY,
            reason_codes=["RISK_KILL_SWITCH_GLOBAL"],
            obligations=[],
            rule_version="v1",
            evaluated_at=datetime.now(UTC),
            expires_at=None,
        )

    monkeypatch.setattr(submit_paper_intent_module, "evaluate_risk_gate", deny_risk_gate)

    with pytest.raises(submit_paper_intent_module.RiskGateDeniedError):
        await submit_paper_intent_module.submit_paper_intent(
            repo,
            adapter,
            *_UNUSED_PORTS,
            deployment_id=deployment.id,
            expected_fence_token=1,
            sequence=1,
            metrics=spy,
        )

    assert spy.counters == []


async def test_submit_paper_intent_fence_superseded_records_no_metric() -> None:
    deployment = _paper_deployment()  # fence_token=1
    repo = _FakePaperControlRepository(deployment)
    adapter = FakePaperExecutionAdapter()
    spy = _SpyMetrics()

    with pytest.raises(submit_paper_intent_module.FenceSupersededError):
        await submit_paper_intent_module.submit_paper_intent(
            repo,
            adapter,
            *_UNUSED_PORTS,
            deployment_id=deployment.id,
            expected_fence_token=999,
            sequence=1,
            metrics=spy,
        )

    assert spy.counters == []
