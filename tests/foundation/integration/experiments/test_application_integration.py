"""AI-11 integration tests -- `application/{record,query,compare}.py` wired
against the real `PostgresExperimentRepository` (AI-10). Spec:
docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4/§9 AI-11 DoD
("재현 키 동일성"). ADR-2026-09-09-C D2: negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1.

Follows `tests/foundation/integration/ai/gateway/test_token_lifecycle.py`'s
pattern -- application functions exercised directly against a real `pool`,
no separate fake-repository unit test layer (this application layer has no
logic that is meaningful without I/O; `domain/lineage.py`'s pure rules
already have their own unit tests in AI-10).
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
from src.foundation.experiments.application.compare import (
    CorruptedReproductionSetError,
    NoReproductionsFoundError,
    ReproducibilityKeyMismatchError,
    compare_by_reproducibility_key,
    compare_experiments,
)
from src.foundation.experiments.application.query import (
    ExperimentNotFoundError,
    get_experiment,
    get_lineage_chain,
    list_reproductions,
)
from src.foundation.experiments.application.record import record_experiment
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.experiments.domain.lineage import (
    DanglingParentError,
    ReproducibilityKeyCollisionError,
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


# --- happy path: record -> query -> compare round trip ---


async def test_record_then_get_round_trip(repo: PostgresExperimentRepository) -> None:
    candidate = _experiment()
    recorded = await record_experiment(repo, candidate)
    assert recorded == candidate

    got = await get_experiment(repo, candidate.tenant_id, candidate.experiment_id)
    assert got.experiment_id == candidate.experiment_id


async def test_get_lineage_chain_returns_root_first(repo: PostgresExperimentRepository) -> None:
    tenant_id = uuid4()
    root = await record_experiment(repo, _experiment(tenant_id=tenant_id))
    child = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, parent_id=root.experiment_id)
    )
    grandchild = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, parent_id=child.experiment_id)
    )

    chain = await get_lineage_chain(repo, tenant_id, grandchild.experiment_id)

    assert [e.experiment_id for e in chain] == [
        root.experiment_id,
        child.experiment_id,
        grandchild.experiment_id,
    ]


async def test_compare_by_reproducibility_key_reports_legitimate_reruns(
    repo: PostgresExperimentRepository,
) -> None:
    tenant_id = uuid4()
    key = "c" * 64
    first = await record_experiment(
        repo,
        _experiment(
            tenant_id=tenant_id,
            reproducibility_key=key,
            inputs_hash="d" * 64,
            metrics={"sharpe": 1.0},
        ),
    )
    second = await record_experiment(
        repo,
        _experiment(
            tenant_id=tenant_id,
            reproducibility_key=key,
            inputs_hash="d" * 64,
            metrics={"sharpe": 1.2},
        ),
    )

    comparison = await compare_by_reproducibility_key(repo, tenant_id, key)

    assert comparison.inputs_hash == "d" * 64
    assert {e.experiment_id for e in comparison.experiments} == {
        first.experiment_id,
        second.experiment_id,
    }
    assert comparison.metrics_by_experiment[first.experiment_id] == {"sharpe": 1.0}
    assert comparison.metrics_by_experiment[second.experiment_id] == {"sharpe": 1.2}
    assert comparison.metric_keys == ("sharpe",)


async def test_compare_experiments_by_explicit_ids_matches_key_comparison(
    repo: PostgresExperimentRepository,
) -> None:
    tenant_id = uuid4()
    key = "1" * 64
    first = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="2" * 64)
    )
    second = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="2" * 64)
    )

    comparison = await compare_experiments(
        repo, tenant_id, [first.experiment_id, second.experiment_id]
    )

    assert comparison.reproducibility_key == key
    assert len(comparison.experiments) == 2


async def test_list_reproductions_is_thin_passthrough(repo: PostgresExperimentRepository) -> None:
    tenant_id = uuid4()
    key = "5" * 64
    recorded = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key)
    )

    found = await list_reproductions(repo, tenant_id, key)

    assert [e.experiment_id for e in found] == [recorded.experiment_id]


# --- negative (>= 3) ---


async def test_record_experiment_rejects_dangling_parent(
    repo: PostgresExperimentRepository,
) -> None:
    orphan = _experiment(parent_id=uuid4())
    with pytest.raises(DanglingParentError):
        await record_experiment(repo, orphan)


async def test_record_experiment_rejects_reproducibility_key_collision(
    repo: PostgresExperimentRepository,
) -> None:
    tenant_id = uuid4()
    key = "6" * 64
    await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="7" * 64)
    )

    with pytest.raises(ReproducibilityKeyCollisionError):
        await record_experiment(
            repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="8" * 64)
        )


async def test_get_experiment_rejects_missing(repo: PostgresExperimentRepository) -> None:
    with pytest.raises(ExperimentNotFoundError):
        await get_experiment(repo, uuid4(), uuid4())


async def test_compare_experiments_rejects_mismatched_reproducibility_key(
    repo: PostgresExperimentRepository,
) -> None:
    tenant_id = uuid4()
    first = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key="a" * 64)
    )
    second = await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key="9" * 64)
    )

    with pytest.raises(ReproducibilityKeyMismatchError):
        await compare_experiments(repo, tenant_id, [first.experiment_id, second.experiment_id])


async def test_compare_by_reproducibility_key_rejects_when_none_found(
    repo: PostgresExperimentRepository,
) -> None:
    with pytest.raises(NoReproductionsFoundError):
        await compare_by_reproducibility_key(repo, uuid4(), "0" * 64)


# --- failure injection: a row bypassing record_experiment corrupts the ---
# --- reproduction set; compare must refuse to present it as identical    ---


async def _insert_raw(pool: asyncpg.Pool, experiment: Experiment) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO experiments "
            "(experiment_id, tenant_id, reproducibility_key, kind, inputs_hash, "
            " metrics, artifacts, parent_id, created_by, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10)",
            experiment.experiment_id,
            experiment.tenant_id,
            experiment.reproducibility_key,
            experiment.kind.value,
            experiment.inputs_hash,
            "{}",
            list(experiment.artifacts),
            experiment.parent_id,
            experiment.created_by,
            experiment.created_at,
        )


async def test_compare_rejects_reproduction_set_corrupted_by_bypassing_record(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """No unique/CHECK constraint on `experiments.reproducibility_key`
    stops a raw INSERT from sharing a key with a different `inputs_hash`
    (confirmed in migration `c3f8a1d29b6e`) -- only `record_experiment`'s
    application-level lineage guard normally prevents that. Bypass it here
    and prove `compare_by_reproducibility_key` still refuses to treat the
    two rows as one reproduction set."""
    tenant_id = uuid4()
    key = "e" * 64
    await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="f" * 64)
    )
    corrupt = _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="0" * 64)
    await _insert_raw(pool, corrupt)

    with pytest.raises(CorruptedReproductionSetError) as exc_info:
        await compare_by_reproducibility_key(repo, tenant_id, key)

    assert exc_info.value.inputs_hashes == frozenset({"f" * 64, "0" * 64})


# --- gate-red reproduction: prove the two rows genuinely diverge ---


async def test_gate_red_corrupted_rows_actually_diverge_on_inputs_hash(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """Proves the failure-injection test above is not a tautology -- reading
    the raw rows back (bypassing `compare`'s guard entirely) shows they
    really do carry two different `inputs_hash` values under one key, so the
    guard's rejection reflects a real divergence, not a false positive."""
    tenant_id = uuid4()
    key = "4" * 64
    await record_experiment(
        repo, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="1" * 64)
    )
    await _insert_raw(
        pool, _experiment(tenant_id=tenant_id, reproducibility_key=key, inputs_hash="2" * 64)
    )

    rows = await repo.find_by_reproducibility_key(tenant_id, key)

    # red: without the guard, this divergence would silently flow into the
    # comparison's metrics_by_experiment as if it were one legitimate rerun.
    assert {row.inputs_hash for row in rows} == {"1" * 64, "2" * 64}


# --- numeric performance assertion ---

_RECORD_DB_ROUNDTRIP_P95_BUDGET_MS = 100.0
"""`record_experiment` does up to three round trips (parent lookup,
same-key lookup, append) vs. `append`'s own single-trip 50ms budget in AI-10's
`test_postgres_repository.py` -- scaled up accordingly, still generous."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_record_experiment_db_roundtrip_p95_within_budget(
    repo: PostgresExperimentRepository,
) -> None:
    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        await record_experiment(repo, _experiment())
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-11 record_experiment] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_RECORD_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _RECORD_DB_ROUNDTRIP_P95_BUDGET_MS
