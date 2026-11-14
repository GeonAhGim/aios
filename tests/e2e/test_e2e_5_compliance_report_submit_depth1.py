"""E2E-5 깊이 하한 보강 (D1 -> D2) -- 음성/실패주입/게이트 적색/성능 단언.

기존 `test_e2e_5_compliance_report_submit.py`의 헬퍼를 그대로 임포트해 쓰고 그
파일은 수정하지 않는다. 추가 증거:
  - 음성 3종: order_id 불일치 / naive generated_at / WORM 중복 store 덮어쓰기 불가
  - 실패 주입: 제출 포트 거부 후 같은 레코드 재제출이 감사 체인을 깨지 않음
  - 게이트 적색 재현: virtual_delayed_async 10배 지연을 주입하면 같은 예산 단언이 적색
  - 성능 단언: perf_budget.best_of_async(여러 번 중 최소) 생성+제출+감사 파이프라인

성능 예산(신설): _BUDGET_MS=600ms (best-of-5 cpu). 로컬 실측은 수십 ms 미만
(Postgres 감사 INSERT 2건 포함)이라 30배 이상 여유. 가상 지연은 예산의 10배(6초)로
sleep 없이 ledger에만 더한다. 기존 예산·단언은 변경하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.contracts.v1 import Outcome
from src.foundation.mandates.reporting.application.generate_report import (
    GenerateReportInputError,
    generate_report,
)
from src.foundation.mandates.reporting.ports.report_submitter import GeneratedReportRecord
from tests.e2e.test_e2e_5_compliance_report_submit import (
    _GENERATE_ACTION,
    InMemoryWormReportStore,
    MockRegulatorySubmissionPort,
    SubmissionRejected,
    _best_execution_evidence,
    _generated_report,
    _post_trade_compliance_decision,
    _record_audit,
    _submit_action_outcome,
    _submit_report_with_audit_trail,
    _trade_report,
)
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_BUDGET_MS = 600.0
_INJECT_S = 6.0  # 예산의 10배를 가상(sleep 없이) 주입


async def test_generate_report_rejects_mismatched_order_ids() -> None:
    """음성 #1 -- trade_report/best_execution order_id 불일치는 fail-closed."""
    store = InMemoryWormReportStore()
    trade_report = _trade_report(uuid4(), _post_trade_compliance_decision())
    with pytest.raises(GenerateReportInputError, match="order_id does not match"):
        await generate_report(
            store,
            trade_report=trade_report,
            best_execution=_best_execution_evidence(uuid4()),
            generated_at=datetime(2026, 9, 24, 6, 0, 10, tzinfo=UTC),
        )
    assert await store.get_by_trade_id(trade_report.trade_id) is None


async def test_generate_report_rejects_naive_generated_at() -> None:
    """음성 #2 -- tz 없는 generated_at 거부, WORM 스토어에 아무것도 남지 않는다."""
    order_id = uuid4()
    store = InMemoryWormReportStore()
    trade_report = _trade_report(order_id, _post_trade_compliance_decision())
    with pytest.raises(GenerateReportInputError, match="tz-aware"):
        await generate_report(
            store,
            trade_report=trade_report,
            best_execution=_best_execution_evidence(order_id),
            generated_at=datetime(2026, 9, 24, 6, 0, 10),
        )
    assert await store.get_by_trade_id(trade_report.trade_id) is None


async def test_worm_store_duplicate_store_cannot_overwrite_first_record() -> None:
    """음성 #3 -- 같은 trade_id의 변조 레코드 store는 최초 레코드를 반환(덮어쓰기 불가)."""
    _order_id, record, store = await _generated_report()
    forged = GeneratedReportRecord(
        trade_id=record.trade_id,
        order_id=record.order_id,
        content_hash="f" * 64,
        fields=dict(record.fields),
        generated_at=record.generated_at,
    )
    assert (await store.store(forged)).content_hash == record.content_hash
    persisted = await store.get_by_trade_id(record.trade_id)
    assert persisted is not None and persisted.content_hash == record.content_hash


async def test_rejected_submission_then_retry_keeps_audit_chain_intact(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입 -- 거부(DENIED) 후 같은 레코드를 재제출해도 감사 해시체인은 유효하고,
    두 시도 모두 감사 이벤트로 남는다(거부가 삼켜지지 않음)."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    _order_id, record, _store = await _generated_report()

    rejecting = MockRegulatorySubmissionPort(outcome="reject")
    with pytest.raises(SubmissionRejected):
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=rejecting,
            stored_record=record,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )
    assert await _submit_action_outcome(pool, tenant_id, record.trade_id) == "DENIED"

    accepting = MockRegulatorySubmissionPort(outcome="accept")
    receipt = await _submit_report_with_audit_trail(
        audit_repo=audit_repo,
        submission_port=accepting,
        stored_record=record,
        expected_content_hash=record.content_hash,
        tenant_id=tenant_id,
        trace_id=uuid4(),
    )
    assert receipt.trade_id == record.trade_id
    assert len(rejecting.submit_calls) == 1 and len(accepting.submit_calls) == 1
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event "
            "WHERE tenant_id = $1 AND aggregate_id = $2",
            tenant_id,
            record.trade_id,
        )
    assert count == 2
    await verify_audit_chain(audit_repo, tenant_id)  # 체인 손상 시 raise


@pytest.mark.perf
async def test_pipeline_cpu_within_budget_and_virtual_delay_turns_gate_red(
    pool: asyncpg.Pool, perf_budget: PerfBudget
) -> None:
    """성능 단언 + 게이트 적색 재현 -- 파이프라인 best-of-5 cpu가 예산 안이고(기준선),
    같은 단계에 10배 가상 지연을 주입하면 동일 단언이 적색이 된다(단언이 실제로 잡음)."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)

    async def _pipeline() -> None:
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

    reset_virtual_totals()
    best = await perf_budget.best_of_async(_pipeline, n=5, warmup=1)
    assert best.cpu_ms < _BUDGET_MS, perf_budget.describe(best, budget_ms=_BUDGET_MS)

    slowed = virtual_delayed_async(_pipeline, _INJECT_S)
    slow_best = await perf_budget.best_of_async(slowed, n=3, warmup=0)
    reset_virtual_totals()
    # 가상 지연은 wall에만 더해진다 -- 예산 판정은 wall 기준으로도 적색이어야 한다.
    assert slow_best.wall_ms >= _INJECT_S * 1000.0
    assert slow_best.wall_ms > _BUDGET_MS, "게이트가 10배 지연을 잡지 못했다"
