"""E2E-5 — 컴플라이언스 사후 판정 -> 규제 보고서 생성 -> 제출 포트(모의) ->
감사 추적, 풀스택.

Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3
— depth 미판정(D?) 리프 개별 재QA 대신 이 시나리오 녹색으로 대체 검증한다.
커버 리프: CM-1(`compliance_decision_from_policy_decision` — 사후 판정),
CM-14(`best_execution`), CM-15(`normalize_trade_report`/
`to_domestic_report_fields`), CM-16(`generate_report` — WORM `content_hash` +
`ReportSubmitterPort.store()`), AUD-003/FND-03(`append_audit_event`/
`verify_audit_chain`, src/foundation/evidence/** 실 Postgres 해시체인).

`ReportSubmitterPort`(reporting/ports/report_submitter.py 모듈 docstring)와
실제 규제기관 제출 어댑터는 spec §10에 따라 MVP-2 스코프 — MVP-1은 보고서
"생성"까지만 실제 구현을 갖는다. 그 경계를 그대로 존중해 WORM 스토어는
`test_generate_report.py`의 `FakeReportStore`와 동일한 insert-or-get 계약
모의 어댑터(`InMemoryWormReportStore`), 규제 제출 포트는
`MockRegulatorySubmissionPort`(승인/거부/타임아웃 계약 테스트)로 두고, 그
앞뒤 실제 구현 경로(CM-1/14/15/16 순수 함수 + AUD-003 실 Postgres 감사
체인)는 실제 소스를 그대로 호출한다.

실패 주입 3건(요구 최소 2건): 거부(REJECT) / 타임아웃(TIMEOUT) / 불일치
(content_hash MISMATCH — INVARIANTS.md I-04 교차검증, CM-16의
`ReportDriftError`와 같은 원칙을 제출 경계에서 재검증). `scripts/
replay_verify.py`는 orders/ledger 전용이라 N/A(no eventstore-backed
order/ledger state) — `verify_audit_chain()`이 같은 역할(재구성 후 해시체인
무결성 증명)을 하므로 D3 "replay_verify 통과"는 그것으로 대체한다
(test_generate_report.py 모듈 docstring과 동일 대체 논리).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.contracts.v1 import Classification, Outcome, RecordAuditEventCommand
from src.foundation.evidence.ports.repository import AuditEventRepository
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
from src.foundation.mandates.reporting.ports.report_submitter import GeneratedReportRecord
from tests.integration.conftest import create_test_tenant

_AGGREGATE_TYPE = "compliance_report"
_SUBMIT_ACTION = "regulatory_report_submit"
_GENERATE_ACTION = "compliance_report_generated"


class InMemoryWormReportStore:
    """`ReportSubmitterPort` 계약 모의 어댑터 -- insert-or-get, no update.
    `test_generate_report.py`의 `FakeReportStore`와 동일 계약. 실제 Postgres
    WORM 어댑터는 이 리프 범위 밖(MVP-2, 모듈 docstring)."""

    def __init__(self) -> None:
        self._rows: dict[UUID, GeneratedReportRecord] = {}

    async def store(self, record: GeneratedReportRecord) -> GeneratedReportRecord:
        existing = self._rows.get(record.trade_id)
        if existing is not None:
            return existing
        self._rows[record.trade_id] = record
        return record

    async def get_by_trade_id(self, trade_id: UUID) -> GeneratedReportRecord | None:
        return self._rows.get(trade_id)


class SubmissionRejected(Exception):
    """규제기관 게이트웨이가 명시적으로 제출을 거부(예: 영업일 마감)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ReportSubmissionMismatchError(Exception):
    """저장 레코드의 `content_hash`가 `generate_report` 확정 해시와 다르다 --
    I-04를 제출 경계에서 재검증, 제출 포트 호출 전에 막는다."""


@dataclass(frozen=True)
class SubmissionReceipt:
    trade_id: UUID
    confirmation_id: str
    submitted_at: datetime


class MockRegulatorySubmissionPort:
    """MVP-2 실 규제 제출 어댑터가 없으므로(report_submitter.py §10) 이
    클래스가 그 경계의 계약 테스트용 모의 어댑터다."""

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
            submitted_at=datetime.now(timezone.utc),
        )


async def _record_audit(
    audit_repo: AuditEventRepository,
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


async def _submit_report_with_audit_trail(
    *,
    audit_repo: AuditEventRepository,
    submission_port: MockRegulatorySubmissionPort,
    stored_record: GeneratedReportRecord,
    expected_content_hash: str,
    tenant_id: UUID,
    trace_id: UUID,
) -> SubmissionReceipt:
    """제출 경계 오케스트레이션(e2e 전용, src에는 아직 없는 MVP-2 배선을
    흉내) -- fail-closed(I-10): 불일치/거부(DENIED)/타임아웃(ERROR)/성공
    (SUCCESS) 모두 감사 이벤트를 남긴다."""
    if stored_record.content_hash != expected_content_hash:
        await _record_audit(
            audit_repo,
            tenant_id=tenant_id,
            trade_id=stored_record.trade_id,
            action=_SUBMIT_ACTION,
            outcome=Outcome.ERROR,
            trace_id=trace_id,
            payload={
                "error": "content_hash_mismatch",
                "stored_hash": stored_record.content_hash,
                "expected_hash": expected_content_hash,
            },
        )
        raise ReportSubmissionMismatchError(
            f"trade {stored_record.trade_id}: stored content_hash="
            f"{stored_record.content_hash} != expected={expected_content_hash}"
        )

    try:
        receipt = await submission_port.submit(stored_record)
    except Exception as exc:
        outcome = Outcome.DENIED if isinstance(exc, SubmissionRejected) else Outcome.ERROR
        await _record_audit(
            audit_repo,
            tenant_id=tenant_id,
            trade_id=stored_record.trade_id,
            action=_SUBMIT_ACTION,
            outcome=outcome,
            trace_id=trace_id,
            payload={"error": str(exc), "content_hash": stored_record.content_hash},
        )
        raise

    await _record_audit(
        audit_repo,
        tenant_id=tenant_id,
        trade_id=stored_record.trade_id,
        action=_SUBMIT_ACTION,
        outcome=Outcome.SUCCESS,
        trace_id=trace_id,
        payload={
            "content_hash": stored_record.content_hash,
            "confirmation_id": receipt.confirmation_id,
        },
    )
    return receipt


def _post_trade_compliance_decision() -> ComplianceDecision:
    """CM-1 실제 경로 -- 실 policy_decision 행을 투영한 "사후 판정"."""
    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.ALLOW,
        reason_codes=["POST_TRADE_REVIEW_PASSED"],
        inputs_hash="a" * 64,
        bundle_version="b" * 64,
        evaluated_at=datetime(2026, 9, 24, 6, 0, 0, tzinfo=timezone.utc),
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
        executed_at=datetime(2026, 9, 24, 6, 0, 5, tzinfo=timezone.utc),
        trader_id="e2e-5-trader",
    )


def _best_execution_evidence(order_id: UUID) -> BestExecutionEvidence:
    now = datetime(2026, 9, 24, 6, 0, 0, tzinfo=timezone.utc)
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
        generated_at=datetime(2026, 9, 24, 6, 0, 10, tzinfo=timezone.utc),
    )
    return order_id, record, store


async def _submit_action_outcome(pool: asyncpg.Pool, tenant_id: UUID, trade_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE tenant_id = $1 AND aggregate_id = $2 AND action = $3",
            tenant_id,
            trade_id,
            _SUBMIT_ACTION,
        )
    assert row is not None
    return str(row["outcome"])


async def test_post_trade_determination_report_submit_and_audit_trail_happy_path(
    pool: asyncpg.Pool,
) -> None:
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)

    order_id, record, store = await _generated_report()

    # --- CM-14/CM-15 수치 단언(Decimal 정확값) ---
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None
    assert stored.fields["수량"] == "10"
    assert stored.fields["단가"] == "70000"
    expected_slippage_bps = (Decimal("70000") - Decimal("69950")) / Decimal("69950") * Decimal(
        "10000"
    )
    assert stored.fields["집행_벤치마크가격"] == "69950"
    assert stored.fields["집행_슬리피지_bp"] == str(expected_slippage_bps)
    assert len(record.content_hash) == 64
    int(record.content_hash, 16)  # hex 검증

    await _record_audit(
        audit_repo,
        tenant_id=tenant_id,
        trade_id=record.trade_id,
        action=_GENERATE_ACTION,
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
        payload={"content_hash": record.content_hash, "order_id": str(order_id)},
    )

    submission_port = MockRegulatorySubmissionPort(outcome="accept")
    receipt = await _submit_report_with_audit_trail(
        audit_repo=audit_repo,
        submission_port=submission_port,
        stored_record=stored,
        expected_content_hash=record.content_hash,
        tenant_id=tenant_id,
        trace_id=uuid4(),
    )

    assert receipt.confirmation_id == f"CONF-{record.trade_id.hex[:8]}"
    assert len(submission_port.submit_calls) == 1

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action, outcome FROM foundation_audit_event "
            "WHERE tenant_id = $1 AND aggregate_id = $2 ORDER BY sequence_no",
            tenant_id,
            record.trade_id,
        )
    assert [(r["action"], r["outcome"]) for r in rows] == [
        (_GENERATE_ACTION, "SUCCESS"),
        (_SUBMIT_ACTION, "SUCCESS"),
    ]

    await verify_audit_chain(audit_repo, tenant_id)  # D3 replay-verify 대체: 체인 무결성 증명


async def test_submission_rejected_records_denied_audit_event_and_worm_row_survives(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입 #1 -- 거부(REJECT). DENIED로 남고 WORM 보고서 행은 생존
    (생성/제출은 별개 경계)."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    _order_id, record, store = await _generated_report()
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None

    submission_port = MockRegulatorySubmissionPort(outcome="reject")
    with pytest.raises(SubmissionRejected, match="MARKET_CLOSED"):
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=submission_port,
            stored_record=stored,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )

    assert len(submission_port.submit_calls) == 1  # 실제로 시도는 됐다(항상-거부 결함이 아님)
    assert await _submit_action_outcome(pool, tenant_id, record.trade_id) == "DENIED"
    # WORM 행은 제출 실패와 무관하게 생존.
    still_stored = await store.get_by_trade_id(record.trade_id)
    assert still_stored == record


async def test_submission_timeout_propagates_and_records_error_not_swallowed(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입 #2 -- 타임아웃. I-10(fail-closed): 예외는 삼키지 않고
    전파, 감사 이벤트는 ERROR로 남는다."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    _order_id, record, store = await _generated_report()
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None

    submission_port = MockRegulatorySubmissionPort(outcome="timeout")
    with pytest.raises(TimeoutError, match="did not respond"):
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=submission_port,
            stored_record=stored,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )

    assert await _submit_action_outcome(pool, tenant_id, record.trade_id) == "ERROR"


async def test_content_hash_mismatch_refused_before_submission_port_is_called(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입 #3 -- 불일치(MISMATCH), I-04 교차검증. 드리프트된 스테일
    읽기를 흉내내면 제출 포트를 호출하지 않고 거부한다(CM-16
    `ReportDriftError`와 같은 불변성을 제출 경계에서 재검증)."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    _order_id, record, store = await _generated_report()
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None
    tampered = GeneratedReportRecord(
        trade_id=stored.trade_id,
        order_id=stored.order_id,
        content_hash="0" * 64,  # 드리프트된 스테일 읽기를 흉내
        fields=stored.fields,
        generated_at=stored.generated_at,
    )

    submission_port = MockRegulatorySubmissionPort(outcome="accept")
    with pytest.raises(ReportSubmissionMismatchError, match="content_hash"):
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=submission_port,
            stored_record=tampered,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )

    assert submission_port.submit_calls == []  # 외부 I/O(제출 포트) 자체가 호출되지 않았다
    assert await _submit_action_outcome(pool, tenant_id, record.trade_id) == "ERROR"


async def _p95_ms(reps: int, step: Callable[[], Awaitable[object]]) -> float:
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await step()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[int(len(samples) * 0.95) - 1]


@pytest.mark.perf
async def test_report_generate_submit_audit_pipeline_latency_within_normalized_budget(
    pool: asyncpg.Pool,
) -> None:
    """수치 성능 단언(D2) -- 생성+제출+감사 왕복 2건 전 구간 p95를 `SELECT 1`
    p95 정규화 임계로 단언(test_order_execution_settlement.py와 동일 관례)."""
    reps = 10
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)

    async def _one_pipeline() -> None:
        _order_id, record, store = await _generated_report()
        stored = await store.get_by_trade_id(record.trade_id)
        assert stored is not None
        await _record_audit(
            audit_repo,
            tenant_id=tenant_id,
            trade_id=record.trade_id,
            action=_GENERATE_ACTION,
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
            payload={"content_hash": record.content_hash},
        )
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=MockRegulatorySubmissionPort(outcome="accept"),
            stored_record=stored,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )

    pipeline_p95 = await _p95_ms(reps, _one_pipeline)
    budget_ms = max(1500.0, 150.0 * baseline_p95)
    print(  # noqa: T201 -- 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nE2E-5 pipeline p95={pipeline_p95:.3f}ms baseline(SELECT 1) "
        f"p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert pipeline_p95 < budget_ms
