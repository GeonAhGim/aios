"""E2E-5 D2 보강: 음성 3종 이상, 제출 포트 실패 주입, 게이트 적색 재현, 성능 단언.

기존 `test_e2e_5_compliance_report_submit.py`의 헬퍼/모의 어댑터를 그대로 재사용한다.
앱 코드는 변경하지 않는다. 기존 단언·예산은 건드리지 않는다.

성능 예산 신설 근거: 2026-11-14 로컬 best-of-3(생성+감사+제출+감사 1회 왕복)
wall 1.72ms(실 Postgres 감사 2행 포함). 예산 2000ms는 실측의 30배 이상 여유이며
DB/CI 경합을 포함한다. 제출 포트에 가상 지연 10배(예산 x10 = 20s)를 주입하면 같은
단언이 적색이어야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.trading import OrderSide
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.contracts.v1 import Outcome
from src.foundation.mandates.reporting.application.generate_report import (
    GenerateReportInputError,
    generate_report,
)
from src.foundation.mandates.reporting.domain.trade_report import (
    TradeReportInputError,
    normalize_trade_report,
)
from src.foundation.mandates.reporting.ports.report_submitter import GeneratedReportRecord
from tests.e2e.test_e2e_5_compliance_report_submit import (
    _GENERATE_ACTION,
    InMemoryWormReportStore,
    MockRegulatorySubmissionPort,
    _best_execution_evidence,
    _generated_report,
    _post_trade_compliance_decision,
    _record_audit,
    _submit_action_outcome,
    _submit_report_with_audit_trail,
    _trade_report,
)
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget, PerfSample
from tests.support.perf_inject import virtual_delayed_async

_PIPELINE_BUDGET_MS = 2000.0


async def test_negative_order_id_mismatch_rejected_and_nothing_stored() -> None:
    order_id = uuid4()
    trade_report = _trade_report(order_id, _post_trade_compliance_decision())
    store = InMemoryWormReportStore()
    with pytest.raises(GenerateReportInputError):
        await generate_report(
            store,
            trade_report=trade_report,
            best_execution=_best_execution_evidence(uuid4()),
            generated_at=datetime(2026, 9, 24, 6, 0, 10, tzinfo=UTC),
        )
    assert await store.get_by_trade_id(trade_report.trade_id) is None


async def test_negative_naive_generated_at_rejected() -> None:
    order_id = uuid4()
    store = InMemoryWormReportStore()
    trade_report = _trade_report(order_id, _post_trade_compliance_decision())
    with pytest.raises(GenerateReportInputError, match="tz-aware"):
        await generate_report(
            store,
            trade_report=trade_report,
            best_execution=_best_execution_evidence(order_id),
            generated_at=datetime(2026, 9, 24, 6, 0, 10),  # noqa: DTZ001 -- 의도된 naive 입력
        )
    assert await store.get_by_trade_id(trade_report.trade_id) is None


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("quantity", Decimal("0"), "quantity"),
        ("price", Decimal("-1"), "price"),
        ("executed_at", datetime(2026, 9, 24, 6, 0, 5), "tz-aware"),  # noqa: DTZ001
    ],
)
def test_negative_trade_report_rejects_invalid_numeric_or_naive_inputs(
    field: str, value: object, match: str
) -> None:
    kwargs: dict[str, Any] = {
        "order_id": uuid4(),
        "trade_id": uuid4(),
        "compliance_decision": _post_trade_compliance_decision(),
        "instrument": "005930",
        "venue": "KRX",
        "side": OrderSide.BUY,
        "quantity": Decimal("10"),
        "price": Decimal("70000"),
        "currency": "KRW",
        "executed_at": datetime(2026, 9, 24, 6, 0, 5, tzinfo=UTC),
        "trader_id": "e2e-5-trader",
    }
    kwargs[field] = value
    with pytest.raises(TradeReportInputError, match=match):
        normalize_trade_report(**kwargs)


async def test_failure_injection_rejected_then_retry_succeeds_and_chain_verifies(
    pool: asyncpg.Pool,
) -> None:
    """거부 후 재시도: 두 시도 모두 감사에 남고(DENIED -> SUCCESS) 해시체인은 무결하다."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    _order_id, record, store = await _generated_report()
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None
    kwargs: dict[str, Any] = {
        "audit_repo": audit_repo,
        "stored_record": stored,
        "expected_content_hash": record.content_hash,
        "tenant_id": tenant_id,
        "trace_id": uuid4(),
    }
    rejecting = MockRegulatorySubmissionPort(outcome="reject")
    with pytest.raises(Exception, match="MARKET_CLOSED"):
        await _submit_report_with_audit_trail(submission_port=rejecting, **kwargs)
    accepting = MockRegulatorySubmissionPort(outcome="accept")
    receipt = await _submit_report_with_audit_trail(submission_port=accepting, **kwargs)
    assert receipt.trade_id == record.trade_id
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE tenant_id = $1 AND aggregate_id = $2 ORDER BY sequence_no",
            tenant_id,
            record.trade_id,
        )
    assert [r["outcome"] for r in rows] == ["DENIED", "SUCCESS"]
    await verify_audit_chain(audit_repo, tenant_id)


def _assert_submitted_hash_matches(
    port: MockRegulatorySubmissionPort, expected_content_hash: str
) -> None:
    assert port.submit_calls, "submission port was never called"
    for sent in port.submit_calls:
        assert sent.content_hash == expected_content_hash, "submitted a drifted report"


async def test_gate_red_hash_assertion_catches_ungated_tampered_submission() -> None:
    """게이트 적색 재현: 해시 게이트를 우회해 변조 레코드를 포트에 직접 넣으면
    제출-해시 단언이 실제로 적색이다(정상 레코드는 녹색)."""
    _order_id, record, store = await _generated_report()
    stored = await store.get_by_trade_id(record.trade_id)
    assert stored is not None
    port = MockRegulatorySubmissionPort(outcome="accept")
    await port.submit(stored)
    _assert_submitted_hash_matches(port, record.content_hash)  # 녹색

    tampered = GeneratedReportRecord(
        trade_id=stored.trade_id,
        order_id=stored.order_id,
        content_hash="f" * 64,
        fields=stored.fields,
        generated_at=stored.generated_at,
    )
    bad_port = MockRegulatorySubmissionPort(outcome="accept")
    await bad_port.submit(tampered)
    with pytest.raises(AssertionError, match="drifted"):
        _assert_submitted_hash_matches(bad_port, record.content_hash)


def _assert_pipeline_budget(sample: PerfSample) -> None:
    assert sample.wall_ms < _PIPELINE_BUDGET_MS, f"E2E-5 pipeline budget: {sample.wall_ms:.3f}ms"


@pytest.mark.perf
async def test_perf_pipeline_best_of_and_gate_red_10x_delay(
    pool: asyncpg.Pool, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """생성+감사+제출+감사 1회 왕복 best-of-3 wall < 2000ms(실측의 30배 이상 여유).
    제출 포트에 예산 10배(20s) 가상 지연을 주입하면 같은 단언이 적색이어야 한다."""
    tenant_id = await create_test_tenant(pool)
    audit_repo = PostgresAuditEventRepository(pool)
    delayed = False
    ports: list[MockRegulatorySubmissionPort] = []

    async def pipeline() -> None:
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
        port = MockRegulatorySubmissionPort(outcome="accept")
        if delayed:
            monkeypatch.setattr(
                port, "submit", virtual_delayed_async(port.submit, _PIPELINE_BUDGET_MS * 10 / 1000)
            )
        ports.append(port)
        await _submit_report_with_audit_trail(
            audit_repo=audit_repo,
            submission_port=port,
            stored_record=stored,
            expected_content_hash=record.content_hash,
            tenant_id=tenant_id,
            trace_id=uuid4(),
        )
        assert await _submit_action_outcome(pool, tenant_id, record.trade_id) == "SUCCESS"

    healthy = await perf_budget.best_of_async(pipeline, n=3, warmup=1)
    print(f"E2E-5 baseline cpu={healthy.cpu_ms:.3f}ms wall={healthy.wall_ms:.3f}ms")
    _assert_pipeline_budget(healthy)

    delayed = True
    degraded = await perf_budget.best_of_async(pipeline, n=3)
    assert degraded.wall_ms >= _PIPELINE_BUDGET_MS * 10
    with pytest.raises(AssertionError, match="E2E-5 pipeline budget"):
        _assert_pipeline_budget(degraded)
    assert all(len(p.submit_calls) == 1 for p in ports)
