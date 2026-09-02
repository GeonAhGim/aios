"""CorrectStatement + GetStatement/ListStatements 통합테스트 — 실제 dev/test
DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (PRF-004), §9
(L49 DoD)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.performance.adapters.paper_input_adapter import PaperStatementInputAdapter
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.application.compute_statement import compute_statement
from src.foundation.performance.application.correct_statement import (
    CrossTenantStatementAccessError,
    StatementNotFoundError,
    correct_statement,
)
from src.foundation.performance.application.get_statement import (
    CrossTenantStatementAccessError as GetCrossTenantError,
)
from src.foundation.performance.application.get_statement import get_statement, list_statements
from src.foundation.performance.contracts.v1 import ComputeStatementCommand, StatementScope
from src.foundation.performance.domain.models import StatementState
from tests.foundation.integration.performance.conftest import set_reconciliation_state
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


async def _compute(pool, repo, inputs, evidence_repo, user_id):
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    return await compute_statement(
        repo,
        inputs,
        evidence_repo,
        tenant_id=user_id,
        cmd=ComputeStatementCommand(
            scope=StatementScope.PAPER,
            scope_ref=str(user_id),
            period_start=_PERIOD_START,
            period_end=_NOW,
        ),
        trace_id=uuid4(),
    )


async def test_correct_statement_creates_successor_and_preserves_original(
    pool, repo, inputs, evidence_repo
):
    user_id = await create_test_user(pool)
    original = await _compute(pool, repo, inputs, evidence_repo, user_id)

    corrected = await correct_statement(
        repo,
        evidence_repo,
        tenant_id=user_id,
        statement_id=original.id,
        reason="가격 소스 재계산",
        trace_id=uuid4(),
    )

    assert corrected.state.value == "CORRECTED"
    assert corrected.prior_statement_id == original.id
    assert corrected.revision_no == original.revision_no + 1
    assert any("가격 소스 재계산" in limitation for limitation in corrected.limitations)

    # 원본은 그대로 남아 있다(WORM) — 값이 바뀌지 않았다.
    original_reloaded = await repo.get_statement(original.id)
    assert original_reloaded is not None
    assert original_reloaded.state == StatementState.ESTIMATED
    assert list(original_reloaded.limitations) == original.limitations


async def test_correct_statement_not_found(repo, evidence_repo):
    with pytest.raises(StatementNotFoundError):
        await correct_statement(
            repo,
            evidence_repo,
            tenant_id=uuid4(),
            statement_id=uuid4(),
            reason="x",
            trace_id=uuid4(),
        )


async def test_correct_statement_cross_tenant_denied(pool, repo, inputs, evidence_repo):
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    original = await _compute(pool, repo, inputs, evidence_repo, owner_id)

    with pytest.raises(CrossTenantStatementAccessError):
        await correct_statement(
            repo,
            evidence_repo,
            tenant_id=other_id,
            statement_id=original.id,
            reason="x",
            trace_id=uuid4(),
        )


async def test_get_statement_cross_tenant_denied(pool, repo, inputs, evidence_repo):
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    original = await _compute(pool, repo, inputs, evidence_repo, owner_id)

    with pytest.raises(GetCrossTenantError):
        await get_statement(repo, tenant_id=other_id, statement_id=original.id)


async def test_list_statements_scoped_to_tenant(pool, repo, inputs, evidence_repo):
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    await _compute(pool, repo, inputs, evidence_repo, owner_id)

    others_view = await list_statements(repo, tenant_id=other_id)

    assert others_view == ()
