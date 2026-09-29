"""L41 rejection and failure wiring; task-7959, INVARIANTS I-07/I-10."""
from __future__ import annotations

import os
from time import perf_counter
from unittest.mock import Mock

import asyncpg
import pytest

from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application.compile_artifact import (
    StrategyNotFoundForCompilationError,
    compile_artifact,
)
from src.foundation.validation.application.run_check import (
    CHECK_RUNNERS,
    UnknownCheckTypeError,
    run_check,
)
from src.services.strategy_builder_service import StrategyBuilderService
from tests.foundation.integration.validation.test_run_check import _ctx_for, _saved_strategy
from tests.integration.conftest import create_test_tenant


@pytest.fixture
async def validation_case():
    dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        service = StrategyBuilderService(pool)
        owner, strategy, version = await _saved_strategy(pool, service)
        yield pool, service, owner, strategy, version
    finally:
        await pool.close()


async def test_negative_cross_tenant_compilation_is_rejected(validation_case):
    pool, service, owner, strategy, version = validation_case
    stranger = await create_test_tenant(pool)
    with pytest.raises(StrategyNotFoundForCompilationError):
        await compile_artifact(
            service, owner_user_id=stranger, strategy_id=strategy, strategy_version=version,
        )
    artifact = await compile_artifact(
        service, owner_user_id=owner, strategy_id=strategy, strategy_version=version,
    )
    assert artifact.strategy_id == strategy


async def test_negative_missing_version_cannot_use_existing_artifact(validation_case):
    _, service, owner, strategy, _ = validation_case
    with pytest.raises(StrategyNotFoundForCompilationError):
        await compile_artifact(
            service, owner_user_id=owner, strategy_id=strategy, strategy_version="missing",
        )


async def test_negative_unknown_check_does_not_persist_run(validation_case):
    pool, service, owner, strategy, version = validation_case
    ctx = await _ctx_for(service, owner, strategy, version)
    with pytest.raises(UnknownCheckTypeError):
        await run_check(
            PostgresValidationRepository(pool), check_type="unregistered",
            ctx=ctx, owner_user_id=owner,
        )
    assert await pool.fetchval(
        "SELECT count(*) FROM strategy_validation_run WHERE strategy_id=$1", strategy,
    ) == 0


async def test_failure_injection_runner_never_persists_success(validation_case, monkeypatch):
    pool, service, owner, strategy, version = validation_case
    repo = PostgresValidationRepository(pool)
    ctx = await _ctx_for(service, owner, strategy, version)
    failure = Mock(side_effect=BacktestRunError("injected engine failure"))
    monkeypatch.setitem(CHECK_RUNNERS, "backtest", failure)
    with pytest.raises(BacktestRunError, match="injected engine failure"):
        await run_check(repo, check_type="backtest", ctx=ctx, owner_user_id=owner)
    failure.assert_called_once_with(ctx)
    row = await pool.fetchrow(
        "SELECT id, state FROM strategy_validation_run WHERE strategy_id=$1", strategy,
    )
    assert row["state"] == "FAILED"
    assert await repo.get_result_for_run(row["id"]) is None


@pytest.mark.perf
async def test_rejection_p95_within_compilation_budget(validation_case):
    """Borrow ADR-2026-09-09-C compilation budget: 300ms, including DB lookup."""
    _, service, owner, strategy, _ = validation_case
    samples = []
    for _ in range(30):
        started = perf_counter()
        with pytest.raises(StrategyNotFoundForCompilationError):
            await compile_artifact(
                service, owner_user_id=owner, strategy_id=strategy, strategy_version="missing",
            )
        samples.append(perf_counter() - started)
    assert sorted(samples)[28] < 0.300
