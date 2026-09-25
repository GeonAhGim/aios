"""L50 — performance 컨텍스트 관측성 배선(로그 필드·메트릭 카운터) 증명.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L50 DoD("각 컨텍스트
로그 필드·메트릭 카운터", "로그 필드 스냅샷 테스트"). `compute_statement()`가
`metrics: MetricsPort` 계측 지점(PLT-10 패턴)과 구조화 로그(`extra={"event":...,
"payload": {...}}`, 108 §2)를 실제로 호출하는지, 실DB 대상으로 검증한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import (
    PERFORMANCE_STATEMENT_COUNT_TOTAL,
    PERFORMANCE_STATEMENT_DURATION_SECONDS,
)
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.performance.adapters.paper_input_adapter import PaperStatementInputAdapter
from src.foundation.performance.adapters.postgres_repository import PostgresPerformanceRepository
from src.foundation.performance.application.compute_statement import (
    MethodologyNotFoundError,
    compute_statement,
)
from src.foundation.performance.contracts.v1 import ComputeStatementCommand, StatementScope
from tests.foundation.integration.performance.conftest import (
    create_paper_execution,
    insert_position,
    set_reconciliation_state,
)
from tests.integration.conftest import create_test_tenant

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


@pytest.fixture
def repo(pool):
    return PostgresPerformanceRepository(pool)


@pytest.fixture
def inputs(pool):
    return PaperStatementInputAdapter(pool)


@pytest.fixture
def evidence_repo(pool):
    return PostgresAuditEventRepository(pool)


def _cmd(scope_ref: str, **overrides: object) -> ComputeStatementCommand:
    defaults: dict[str, object] = dict(
        scope=StatementScope.PAPER,
        scope_ref=scope_ref,
        period_start=_PERIOD_START,
        period_end=_NOW,
    )
    defaults.update(overrides)
    return ComputeStatementCommand(**defaults)


async def test_compute_statement_records_completed_counter_and_duration(
    pool, repo, inputs, evidence_repo
):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    execution_id = await create_paper_execution(pool, user_id, allocated_capital=Decimal("1000"))
    await insert_position(
        pool,
        user_id,
        execution_id,
        entry_time=_NOW - timedelta(hours=1),
        realized_pnl=Decimal("2"),
        unrealized_pnl=Decimal("3"),
    )
    spy = _SpyMetrics()

    await compute_statement(
        repo,
        inputs,
        evidence_repo,
        tenant_id=user_id,
        cmd=_cmd(str(user_id)),
        trace_id=uuid4(),
        metrics=spy,
    )

    assert (
        PERFORMANCE_STATEMENT_COUNT_TOTAL,
        {"op": "compute", "outcome": "completed"},
    ) in spy.counters
    durations = [o for o in spy.observations if o[0] == PERFORMANCE_STATEMENT_DURATION_SECONDS]
    assert len(durations) == 1
    _, value, labels = durations[0]
    assert value >= 0.0
    assert labels == {"op": "compute", "outcome": "completed"}


async def test_compute_statement_unknown_methodology_records_failed_counter_not_completed(
    pool, repo, inputs, evidence_repo
):
    """negative + 실패 주입 — 존재하지 않는 methodology_version을 주입하면
    `MethodologyNotFoundError`가 나고, "completed" 카운터는 절대 기록되지
    않으면서 "failed" 카운터만 기록돼야 한다(오탐 방지, PLT-10 계약)."""
    user_id = await create_test_tenant(pool)
    spy = _SpyMetrics()

    with pytest.raises(MethodologyNotFoundError):
        await compute_statement(
            repo,
            inputs,
            evidence_repo,
            tenant_id=user_id,
            cmd=_cmd(str(user_id), methodology_version="does-not-exist-v9"),
            trace_id=uuid4(),
            metrics=spy,
        )

    assert (
        PERFORMANCE_STATEMENT_COUNT_TOTAL,
        {"op": "compute", "outcome": "failed"},
    ) in spy.counters
    assert (
        PERFORMANCE_STATEMENT_COUNT_TOTAL,
        {"op": "compute", "outcome": "completed"},
    ) not in spy.counters


async def test_compute_statement_without_metrics_arg_defaults_to_null_metrics_and_does_not_crash(
    pool, repo, inputs, evidence_repo
):
    """negative — 기존 호출부(메트릭 인자 없이 호출)는 NullMetrics로 대체돼
    아무 영향 없이 계속 동작해야 한다(하위호환)."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    view = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    assert view.revision_no == 1


async def test_compute_statement_completed_log_field_snapshot(
    pool, repo, inputs, evidence_repo, caplog
):
    """로그 필드 스냅샷 테스트 — `performance_statement_computed` 이벤트가
    `extra={"event":..., "duration_ms":..., "payload": {...}}` 채널로 정확히
    어떤 필드를 싣는지 스냅샷으로 고정한다(필드 추가·누락 모두 이 테스트가 잡는다)."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    with caplog.at_level(
        logging.INFO, logger="src.foundation.performance.application.compute_statement"
    ):
        view = await compute_statement(
            repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
        )

    records = [
        r for r in caplog.records if getattr(r, "event", None) == "performance_statement_computed"
    ]
    assert len(records) == 1
    record = records[0]
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0
    payload = record.payload
    assert set(payload.keys()) == {"tenant_id", "statement_id", "scope", "revision_no"}
    assert payload["tenant_id"] == str(user_id)
    assert payload["statement_id"] == str(view.id)
    assert payload["scope"] == "PAPER"
    assert payload["revision_no"] == 1
