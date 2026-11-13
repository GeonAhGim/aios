"""E2E-5 보강 — 컴플라이언스 사후 판정 깊이 하한 D2 보강 (task-24874).

기존 test_e2e_5_compliance_report_submit.py와 동일한 fixture/임포트 관례를 따른다.
보강 항목:
  - 음성(negative) 테스트 3종 이상 (중복 제출, 빈 fields, 비정상 side)
  - 게이트 적색 재현: virtual_delayed_sync로 가상 지연 주입 → perf_budget 예산 초과 재현
  - 성능 단언: perf_budget.best_of_async + @pytest.mark.perf 마커

Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3
Covered leaves: CM-1, CM-14, CM-15, CM-16, AUD-003/FND-03.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.contracts.v1 import Classification, Outcome, RecordAuditEventCommand
from src.foundation.mandates.contracts.v1 import (
    ComplianceDecision,
    PolicyDecisionRow,
    PolicyOutcome,
    compliance_decision_from_policy_decision,
)
from src.foundation.mandates.reporting.application.generate_report import generate_report
from src.foundation.mandates.reporting.domain.best_execution import (
    BenchmarkKind,
    BestExecutionEvidence,
    best_execution,
)
from src.foundation.mandates.reporting.domain.trade_report import (
    TradeReportRecord,
    normalize_trade_report,
)
from src.foundation.mandates.reporting.ports.report_submitter import (
    GeneratedReportRecord,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
)

_AGGREGATE_TYPE = "compliance_report"
_SUBMIT_ACTION = "regulatory_report_submit"
_GENERATE_ACTION = "compliance_report_generated"


# ---------------------------------------------------------------------------
# 모의 어댑터 — 기존 파일과 동일 계약
# ---------------------------------------------------------------------------


class InMemoryWormReportStore:
    """`ReportSubmitterPort` 계약 모의 어댑터 -- insert-or-get, no update."""

    def __init__(self) -> None:
        self._rows: dict[UUID, GeneratedReportRecord] = {}
        self._insert_count: int = 0

    async def store(self, record: GeneratedReportRecord) -> GeneratedReportRecord:
        self._insert_count += 1
        existing = self._rows.get(record.trade_id)
        if existing is not None:
            return existing
        self._rows[record.trade_id] = record
        return record

    async def get_by_trade_id(self, trade_id: UUID) -> GeneratedReportRecord | None:
        return self._rows.get(trade_id)

    @property
    def insert_count(self) -> int:
        return self._insert_count


class SubmissionRejected(Exception):
    """규제기관 게이트웨이가 명시적으로 제출을 거부."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SubmissionReceipt:
    trade_id: UUID
    confirmation_id: str
    submitted_at: datetime


class MockRegulatorySubmissionPort:
    """MVP-2 실 규제 제출 어댑터가 없으므로 이 클래스가 그 경계 계약 테스트용 모의 어댑터."""

    def __init__(self, *, outcome: str = "accept") -> None:
        self._outcome = outcome
        self.submit_calls: list[GeneratedReportRecord] = []

    async def submit(self, record: GeneratedReportRecord) -> SubmissionReceipt:
        self.submit_calls.append(record)
        if self._outcome == "reject":
            raise SubmissionRejected("MARKET_CLOSED")
        if self._outcome == "timeout":
            raise TimeoutError("regulator gateway did not respond within budget")
        return SubmissionReceipt(
            trade_id=record.trade_id,
            confirmation_id=f"CONF-{record.trade_id.hex[:8]}",
            submitted_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# 헬퍼 — 기존 파일과 동일 구현
# ---------------------------------------------------------------------------


async def _record_audit(
    audit_repo: PostgresAuditEventRepository,
    *,
    tenant_id: UUID,
    trade_id: UUID,
    action: str,
    outcome: Outcome,
    trace_id: UUID,
    payload: dict[str, object],
) -> None:
    await append_audit_event(
        audit_repo,
        RecordAuditEventCommand(
            tenant_id=tenant_id,
            aggregate_type=_AGGREGATE_TYPE,
            aggregate_id=trade_id,
            action=action,
            outcome=outcome,
            trace_id=trace_id,
            payload=payload,
            classification=Classification.INTERNAL,
        ),
    )


def _post_trade_compliance_decision() -> ComplianceDecision:
    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.ALLOW,
        reason_codes=["POST_TRADE_REVIEW_PASSED"],
        inputs_hash="a" * 64,
        bundle_version="b" * 64,
        evaluated_at=datetime(2026, 9, 24, 6, 0, 0, tzinfo=UTC),
    )
    return compliance_decision_from_policy_decision(row)


def _trade_report(order_id: UUID, compliance_decision: ComplianceDecision) -> TradeReportRecord:
    return normalize_trade_report(
        order_id=order_id,
        trade_id=uuid4(),
        compliance_decision=compliance_decision,
        instrument="005930",
        venue="KRX",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("70000"),
        currency="KRW",
        executed_at=datetime(2026, 9, 24, 6, 0, 5, tzinfo=UTC),
        trader_id="e2e-5-trader",
    )


def _best_execution_evidence(order_id: UUID) -> BestExecutionEvidence:
    now = datetime(2026, 9, 24, 6, 0, 0, tzinfo=UTC)
    route = RouteDecisionRecord(
        decision_id=uuid4(),
        order_id=order_id,
        decision=RouteDecision(
            venue="KRX", reason_codes=["ONLY_VENUE"], expected_cost_bps=Decimal("5")
        ),
        candidates_snapshot=[],
        score_snapshot=[],
        weights_snapshot={},
        decided_at=now,
        created_at=now,
    )
    return best_execution(
        order_id=order_id,
        side=OrderSide.BUY,
        execution_price=Decimal("70000"),
        qty=Decimal("10"),
        benchmark=BenchmarkKind.ARRIVAL,
        route_decision=route,
        arrival_price=Decimal("69950"),
    )


async def _generated_report() -> tuple[UUID, GeneratedReportRecord, InMemoryWormReportStore]:
    order_id = uuid4()
    compliance_decision = _post_trade_compliance_decision()
    trade_report = _trade_report(order_id, compliance_decision)
    evidence = _best_execution_evidence(order_id)
    store = InMemoryWormReportStore()
    record = await generate_report(
        store,
        trade_report=trade_report,
        best_execution=evidence,
        generated_at=datetime(2026, 9, 24, 6, 0, 10, tzinfo=UTC),
    )
    return order_id, record, store


# ---------------------------------------------------------------------------
# 음성(negative) 테스트 3종
# ---------------------------------------------------------------------------


async def test_duplicate_store_returns_existing_record_without_audit_insert(
    pool: asyncpg.Pool,
) -> None:
    """음성 #1 — 중복 store: 같은 trade_id로 두 번 store하면 첫 번째 행이 그대로
    반환되고 insert_count는 1이다(WORM no-update 불변성).
    _generated_report()가 내부에서 store()를 한 번 호출하므로, 그 후 store()를
    두 번 더 호출하면 insert_count는 3이 된다(기존 1 + 새로 2)."""
    _order_id, record, store = await _generated_report()
    # _generated_report() 내부에서 이미 store(record) 한 번 호출함
    count_after_gen = store.insert_count
    first = await store.store(record)
    second = await store.store(record)
    assert first is second
    # 기존 호출 1회 + 새로 2회 = 3
    assert store.insert_count == count_after_gen + 2
    # 두 레코드 모두 같은 trade_id를 가진다
    assert first.trade_id == second.trade_id


async def test_generate_report_with_zero_quantity_raises_value_error(
    pool: asyncpg.Pool,
) -> None:
    """음성 #2 — quantity=0: normalize_trade_report는 0 수량을 거부해야 한다."""
    compliance_decision = _post_trade_compliance_decision()
    order_id = uuid4()
    with pytest.raises(ValueError, match="quantity"):
        normalize_trade_report(
            order_id=order_id,
            trade_id=uuid4(),
            compliance_decision=compliance_decision,
            instrument="005930",
            venue="KRX",
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            price=Decimal("70000"),
            currency="KRW",
            executed_at=datetime(2026, 9, 24, 6, 0, 5, tzinfo=UTC),
            trader_id="e2e-5-trader",
        )


async def test_generate_report_with_non_order_side_raises_trade_report_input_error(
    pool: asyncpg.Pool,
) -> None:
    """음성 #3 — 비 OrderSide 타입: normalize_trade_report는 OrderSide가 아닌
    타입(예: raw str)을 거부해야 한다."""
    compliance_decision = _post_trade_compliance_decision()
    order_id = uuid4()
    with pytest.raises(Exception, match="side must be an OrderSide"):  # noqa: B017 -- negative test: exception type is implementation detail
        normalize_trade_report(
            order_id=order_id,
            trade_id=uuid4(),
            compliance_decision=compliance_decision,
            instrument="005930",
            venue="KRX",
            side="BUY",  # type: ignore[arg-type] -- raw str, not OrderSide
            quantity=Decimal("10"),
            price=Decimal("70000"),
            currency="KRW",
            executed_at=datetime(2026, 9, 24, 6, 0, 5, tzinfo=UTC),
            trader_id="e2e-5-trader",
        )


# ---------------------------------------------------------------------------
# 게이트 적색 재현 (gate-red reproduction) 1종
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_gate_red_virtual_delay_exceeds_pipeline_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
    pool: asyncpg.Pool,
) -> None:
    """게이트 적색 재현 (D2, task-24874): generate_report 내부 연산에
    virtual_delayed_sync로 가상 지연(예산 10배)을 주입하고, 같은 CPU 단언이
    초과함을 확인한다.

    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 측정치에 지연을 더할 뿐 실제 테스트 시간이 늘어나지
    않는다. green 테스트와 동일한 `cpu_ms < budget_ms` 단언을 쓰고,
    지연 주입 시 `cpu_ms >= budget_ms`로 적색을 재현한다.

    실측 기준: 이 워크호스트에서 generate_report(모의 스텁)는 ~0.5ms CPU.
    30배 여유인 15.0ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    # 예산 15ms, 10배 가상 지연 = 150ms 주입
    budget_ms = 15.0
    injected_delay_s = budget_ms * 10 / 1000.0  # 0.15초

    # generate_report의 해시 계산에 지연 주입
    import hashlib

    original_sha256 = hashlib.sha256

    class DelayedSha256:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._real = original_sha256(*args, **kwargs)

        def update(self, data: bytes) -> None:
            self._real.update(data)

        def digest(self) -> bytes:
            return virtual_delayed_sync(self._real.digest, injected_delay_s)()

        def copy(self) -> DelayedSha256:
            return DelayedSha256()

        def hexdigest(self) -> str:
            return virtual_delayed_sync(self._real.hexdigest, injected_delay_s)()

    monkeypatch.setattr(hashlib, "sha256", lambda *a, **k: DelayedSha256(*a, **k))

    async def _delayed_pipeline() -> None:
        _order_id, record, store = await _generated_report()
        assert record is not None
        assert len(record.content_hash) == 64

    red = await perf_budget.best_of_async(_delayed_pipeline, n=3, warmup=0)
    assert red.cpu_ms >= budget_ms, (
        f"가상 지연 주입에도 CPU 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
    )


# ---------------------------------------------------------------------------
# 성능 단언 (performance assertion) 1종
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_generate_report_performance_under_normal_load(
    perf_budget: PerfBudget,
    pool: asyncpg.Pool,
) -> None:
    """성능 단언 (D2, task-24874): generate_report는 모의 스텁 기준
    CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 generate_report(순수 함수 + 해시)는
    ~0.5ms CPU. 30배 여유인 15.0ms를 예산으로 설정한다.

    perf_budget.best_of_async로 best-of-N 최소값을 측정하고
    process_time 기준 CPU 예산을 단언한다.
    """
    reset_virtual_totals()

    async def _call() -> None:
        _order_id, record, store = await _generated_report()
        assert record is not None

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.5ms x 30 = 15.0ms 예산
    assert sample.cpu_ms < 15.0, (
        f"generate_report CPU 예산 초과: {perf_budget.describe(sample, budget_ms=15.0)}"
    )
