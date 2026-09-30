"""AI-11 DEEPEN — negative / failure-injection tests for the experiments module.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-11 DoD
("재현 키 동일성"). ADR-2026-09-09-C D2: negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1.

These tests exercise `domain/lineage.py` invariants and application-layer
failure paths directly, without requiring a full DB round trip for every case.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.experiments.adapters.postgres_repository import (
    PostgresExperimentRepository,
)
from src.foundation.experiments.application.query import list_reproductions
from src.foundation.experiments.application.record import record_experiment
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.experiments.domain.lineage import (
    CrossTenantParentError,
    DanglingParentError,
    LineageError,
    ReproducibilityKeyCollisionError,
    validate_new_experiment,
)

_NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresExperimentRepository:
    return PostgresExperimentRepository(pool)


def _experiment(**overrides: Any) -> Experiment:
    base: dict[str, Any] = dict(
        experiment_id=uuid4(),
        tenant_id=uuid4(),
        reproducibility_key="a" * 64,
        kind=ExperimentKind.BACKTEST,
        inputs_hash="b" * 64,
        metrics={"sharpe": 1.5},
        artifacts=(),
        parent_id=None,
        created_by=uuid4(),
        created_at=_NOW,
    )
    base.update(overrides)
    return Experiment(**base)


# ---------------------------------------------------------------------------
# Negative tests — invariant violations must be rejected
# ---------------------------------------------------------------------------


def test_validate_new_experiment_raises_on_dangling_parent() -> None:
    """A candidate with `parent_id` set but `parent=None` must raise DanglingParentError."""
    candidate = _experiment(parent_id=uuid4())
    with pytest.raises(DanglingParentError) as exc:
        validate_new_experiment(candidate, parent=None, existing_with_same_key=None)
    assert "does not reference an existing experiment" in str(exc.value)


def test_validate_new_experiment_raises_on_cross_tenant_parent() -> None:
    """A child whose resolved parent belongs to a different tenant must raise
    CrossTenantParentError."""
    child_tenant = uuid4()
    parent_tenant = uuid4()
    parent_exp = _experiment(tenant_id=parent_tenant)
    candidate = _experiment(tenant_id=child_tenant, parent_id=parent_exp.experiment_id)
    with pytest.raises(CrossTenantParentError) as exc:
        validate_new_experiment(candidate, parent=parent_exp, existing_with_same_key=None)
    assert "does not match candidate tenant" in str(exc.value)


def test_validate_new_experiment_raises_on_reproducibility_key_collision() -> None:
    """Two experiments sharing a reproducibility_key but with different inputs_hash must raise."""
    key = "c" * 64
    candidate = _experiment(reproducibility_key=key, inputs_hash="d" * 64)
    existing = _experiment(reproducibility_key=key, inputs_hash="e" * 64)
    with pytest.raises(ReproducibilityKeyCollisionError) as exc:
        validate_new_experiment(candidate, parent=None, existing_with_same_key=existing)
    assert "collides with a different inputs_hash" in str(exc.value)


async def test_record_experiment_rejects_dangling_parent_via_repo(
    repo: PostgresExperimentRepository,
) -> None:
    """record_experiment must fail when the parent_id points to a non-existent experiment."""
    fake_parent = uuid4()
    candidate = _experiment(parent_id=fake_parent)
    with pytest.raises(LineageError):
        await record_experiment(repo, candidate)


async def test_record_experiment_rejects_reproducibility_key_collision(
    repo: PostgresExperimentRepository,
) -> None:
    """record_experiment must fail when another experiment in the same tenant
    shares the reproducibility_key but has a different inputs_hash."""
    tenant_id = uuid4()
    key = "f" * 64
    await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="1" * 64)
    )
    with pytest.raises(ReproducibilityKeyCollisionError):
        await record_experiment(
            repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="2" * 64)
        )


async def test_list_reproductions_returns_empty_when_none_exist(
    repo: PostgresExperimentRepository,
) -> None:
    """list_reproductions returns an empty tuple for a key with zero experiments
    (no wrapper error — the docstring says "no wrapping error needed")."""
    result = await list_reproductions(repo, uuid4(), "z" * 64)
    assert result == ()


# ---------------------------------------------------------------------------
# Failure-injection test — mock repository raises during parent lookup
# ---------------------------------------------------------------------------


async def test_record_experiment_handles_repository_exception_during_parent_lookup() -> None:
    """When the repository raises on parent lookup, record_experiment should
    propagate the exception."""
    mock_repo = AsyncMock(spec=PostgresExperimentRepository)
    mock_repo.get = AsyncMock(side_effect=ConnectionError("mock connection lost"))
    mock_repo.find_by_reproducibility_key = AsyncMock(return_value=[])
    mock_repo.append = AsyncMock()

    candidate = _experiment(parent_id=uuid4())
    with pytest.raises(ConnectionError, match="mock connection lost"):
        await record_experiment(mock_repo, candidate)

    # Verify that append was never called (validation/lookup failure short-circuits)
    mock_repo.append.assert_not_called()


async def test_record_experiment_handles_repository_exception_during_same_key_lookup() -> None:
    """When the repository raises during same-key lookup, record_experiment should propagate."""
    mock_repo = AsyncMock(spec=PostgresExperimentRepository)
    mock_repo.get = AsyncMock(return_value=None)  # no parent found
    mock_repo.find_by_reproducibility_key = AsyncMock(side_effect=TimeoutError("query timed out"))
    mock_repo.append = AsyncMock()

    candidate = _experiment(parent_id=None)
    with pytest.raises(TimeoutError, match="query timed out"):
        await record_experiment(mock_repo, candidate)

    mock_repo.append.assert_not_called()


# ---------------------------------------------------------------------------
# Numeric performance assertion
# ---------------------------------------------------------------------------

_RECORD_LINEAGE_VALIDATE_BUDGET_MS = 5.0
"""`validate_new_experiment` is pure in-memory — parent/exp lookups are done
by the caller. This budget is generous for three attribute comparisons + hash checks."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
def test_validate_new_experiment_p95_within_budget() -> None:
    """Pure lineage validation must complete well under 5ms p95 for 100 iterations."""
    tenant_id = uuid4()
    parent_exp = _experiment(tenant_id=tenant_id)
    candidate = _experiment(tenant_id=tenant_id, parent_id=parent_exp.experiment_id)

    samples: list[float] = []
    for _ in range(100):
        started = time.perf_counter()
        validate_new_experiment(candidate, parent=parent_exp, existing_with_same_key=None)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-11 validate_new_experiment] p95={p95_ms:.4f}ms "
        f"budget<{_RECORD_LINEAGE_VALIDATE_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _RECORD_LINEAGE_VALIDATE_BUDGET_MS
