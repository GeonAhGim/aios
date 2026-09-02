"""ComputeStatement 통합테스트 — 실제 dev/test DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (PRF-001·002·009),
§9 (L49 DoD)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.performance.adapters.paper_input_adapter import (
    PaperStatementInputAdapter,
    UnreconciledInputError,
)
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.application.compute_statement import compute_statement
from src.foundation.performance.contracts.v1 import ComputeStatementCommand, StatementScope
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import StatementState
from tests.foundation.integration.performance.conftest import (
    create_paper_execution,
    insert_position,
    set_reconciliation_state,
)
from tests.integration.conftest import create_test_user

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


@pytest.fixture
def repo(pool):
    return PostgresPerformanceRepository(pool)


@pytest.fixture
def inputs(pool):
    return PaperStatementInputAdapter(pool)


@pytest.fixture
def evidence_repo(pool):
    return PostgresAuditEventRepository(pool)


def _cmd(scope_ref: str) -> ComputeStatementCommand:
    return ComputeStatementCommand(
        scope=StatementScope.PAPER,
        scope_ref=scope_ref,
        period_start=_PERIOD_START,
        period_end=_NOW,
    )


async def test_compute_statement_raises_when_unreconciled(pool, repo, inputs, evidence_repo):
    """PRF-002 계열 — 미리컨실 입력은 계산을 거부한다(라우터가 이 예외를 409로
    매핑한다)."""
    user_id = await create_test_user(pool)

    with pytest.raises(UnreconciledInputError):
        await compute_statement(
            repo,
            inputs,
            evidence_repo,
            tenant_id=user_id,
            cmd=_cmd(str(user_id)),
            trace_id=uuid4(),
        )


async def test_compute_statement_marks_missing_components_pending_not_zero(
    pool, repo, inputs, evidence_repo
):
    """PRF-002 — fee/slippage/funding/fx/estimated_tax는 원장에 없어 항상
    None(PENDING)이어야 한다. 0으로 대체하지 않는다."""
    user_id = await create_test_user(pool)
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

    view = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    assert view.components.fees.amount is None
    assert view.components.slippage.amount is None
    assert view.components.funding.amount is None
    assert view.components.estimated_tax.amount is None
    assert view.components.gross_pnl.amount == Decimal("5")
    assert view.identity_ok is False
    assert view.state.value == "ESTIMATED"
    assert view.revision_no == 1
    assert view.methodology_hash == DEFAULT_METHODOLOGY.methodology_hash
    assert any("COMPONENTS_LEDGER_INCOMPLETE" in limitation for limitation in view.limitations)
    assert len(view.evidence_refs) == 1


async def test_compute_statement_second_call_increments_revision(
    pool, repo, inputs, evidence_repo
):
    user_id = await create_test_user(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    first = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )
    second = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    assert first.revision_no == 1
    assert second.revision_no == 2
    assert second.prior_statement_id == first.id


async def test_compute_statement_persists_estimated_state(pool, repo, inputs, evidence_repo):
    user_id = await create_test_user(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    view = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    stored = await repo.get_statement(view.id)
    assert stored is not None
    assert stored.state == StatementState.ESTIMATED
    assert stored.tenant_id == user_id
