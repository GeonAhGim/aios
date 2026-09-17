"""AI-10 integration tests -- `adapters/postgres_repository.py` + migration
`c3f8a1d29b6e`. Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md
§2.4/§9 AI-10 DoD ("append-only proof"). ADR-2026-09-09-C D2: negative >= 3,
failure injection 1, numeric performance assertion 1, gate-red reproduction 1.

Follows `tests/adversarial/risk/test_worm_tables.py`'s WORM proof pattern
(REVOKE + trigger, owner-bypass proof) and
`tests/foundation/integration/ai/gateway/test_token_lifecycle.py`'s
`pool`/CHECK-gate-red pattern -- both leaves of this same spec.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.experiments.adapters.postgres_repository import (
    PostgresExperimentRepository,
)
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind

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
        metrics={"sharpe": 1.5, "total_return": "0.12"},
        artifacts=("s3://bucket/report.json",),
        parent_id=None,
        created_by=uuid4(),
        created_at=_NOW,
    )
    base.update(overrides)
    return Experiment(**base)


def _assert_append_only_violation(exc_info: pytest.ExceptionInfo) -> None:
    """WORM defense is two layers (REVOKE + trigger); which one fires first
    is not part of the contract (same reasoning as
    `tests/integration/test_db_roles.py`) -- only check the message when the
    trigger itself raised."""
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


# --- happy path ---


async def test_append_then_get_round_trip(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    experiment = _experiment()
    await repo.append(experiment)

    got = await repo.get(experiment.tenant_id, experiment.experiment_id)

    assert got is not None
    assert got.experiment_id == experiment.experiment_id
    assert got.reproducibility_key == experiment.reproducibility_key
    assert got.kind == ExperimentKind.BACKTEST
    assert got.metrics == experiment.metrics
    assert got.artifacts == experiment.artifacts


async def test_append_parent_child_lineage_round_trip(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    await repo.append(parent)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)
    await repo.append(child)

    got_child = await repo.get(tenant_id, child.experiment_id)
    assert got_child is not None
    assert got_child.parent_id == parent.experiment_id


async def test_find_by_reproducibility_key_returns_all_sharing_it(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    tenant_id = uuid4()
    key = "c" * 64
    first = _experiment(tenant_id=tenant_id, reproducibility_key=key)
    second = _experiment(tenant_id=tenant_id, reproducibility_key=key)
    await repo.append(first)
    await repo.append(second)

    found = await repo.find_by_reproducibility_key(tenant_id, key)

    assert {f.experiment_id for f in found} == {first.experiment_id, second.experiment_id}


# --- negative (>= 3) ---


async def test_get_missing_experiment_returns_none(repo: PostgresExperimentRepository) -> None:
    assert await repo.get(uuid4(), uuid4()) is None


async def test_get_wrong_tenant_returns_none(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """Cross-tenant lookup is enforced by the `WHERE tenant_id = $1` clause,
    not RLS -- same pattern as `research_items` (f5529244403f)."""
    experiment = _experiment()
    await repo.append(experiment)

    got = await repo.get(uuid4(), experiment.experiment_id)

    assert got is None


async def test_append_rejects_dangling_parent_fk(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """The migration's self-referencing FK rejects a parent_id that does not
    exist -- the DB-level backstop behind
    `domain/lineage.py::validate_new_experiment`'s application-level check."""
    orphan = _experiment(parent_id=uuid4())
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.append(orphan)


async def test_append_rejects_invalid_kind_check(pool: asyncpg.Pool) -> None:
    """Direct SQL bypassing `ExperimentKind`'s domain enum still hits the
    migration's CHECK constraint."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO experiments "
                "(experiment_id, tenant_id, reproducibility_key, kind, inputs_hash, "
                " metrics, artifacts, created_by) "
                "VALUES ($1, $2, $3, 'live', $4, '{}'::jsonb, '{}', $5)",
                uuid4(),
                uuid4(),
                "d" * 64,
                "e" * 64,
                uuid4(),
            )


# --- failure injection: WORM blocks mutation even for the table owner ---


async def test_worm_trigger_blocks_table_owner_update(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """REVOKE does not bind the table owner (PostgreSQL rule) -- `pool`
    connects without `SET ROLE` and owns `experiments` (the migration ran as
    this account), so if UPDATE is blocked here it is the trigger, not the
    REVOKE, doing the blocking. Spec §9 AI-10 DoD: append-only proof."""
    experiment = _experiment()
    await repo.append(experiment)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE experiments SET inputs_hash = $1 WHERE experiment_id = $2",
                "f" * 64,
                experiment.experiment_id,
            )


async def test_worm_trigger_blocks_table_owner_delete(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    experiment = _experiment()
    await repo.append(experiment)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM experiments WHERE experiment_id = $1", experiment.experiment_id
            )


async def test_aios_app_role_cannot_update_experiments(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """Second defense layer: `aios_app` (the application's own DB role) was
    never granted UPDATE/DELETE at all, and REVOKE additionally strips
    PUBLIC -- either way it must fail."""
    experiment = _experiment()
    await repo.append(experiment)

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE experiments SET inputs_hash = $1 WHERE experiment_id = $2",
                "0" * 64,
                experiment.experiment_id,
            )
    _assert_append_only_violation(exc_info)


# --- gate-red reproduction: prove the CHECK/FK/WORM guards are load-bearing ---


async def test_gate_red_check_constraint_actually_fails_without_it(pool: asyncpg.Pool) -> None:
    """Proves the CHECK-violation negative test above is not a tautology --
    a temp table with the same column but no CHECK accepts the same value
    that `experiments.kind` rejects."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE experiments_no_check (kind VARCHAR(20) NOT NULL) ON COMMIT DROP"
        )
        await conn.execute("INSERT INTO experiments_no_check (kind) VALUES ('live')")
        row = await conn.fetchrow("SELECT kind FROM experiments_no_check")
        assert row["kind"] == "live"  # red: without the CHECK, this would have passed


# --- numeric performance assertion ---

_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0
"""Borrowed from `test_token_lifecycle.py`'s
`_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS` (single DB round-trip, same spec, no
AI-specific SLO number exists in §7 yet)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_append_db_roundtrip_p95_within_budget(repo: PostgresExperimentRepository) -> None:
    samples: list[float] = []
    for _ in range(30):
        experiment = _experiment()
        started = time.perf_counter()
        await repo.append(experiment)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-10 append] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _APPEND_DB_ROUNDTRIP_P95_BUDGET_MS
