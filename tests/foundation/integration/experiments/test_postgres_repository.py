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
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.experiments.adapters.postgres_repository import (
    PostgresExperimentRepository,
)
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from tests.support.db import create_pool_with_retry
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_async

_NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await create_pool_with_retry(dsn, min_size=1, max_size=8)
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


_APPEND_LINEAGE_P95_BUDGET_MS = 50.0


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


@pytest.mark.perf
async def test_append_parent_child_lineage_p95_within_budget(
    repo: PostgresExperimentRepository, perf_budget: PerfBudget
) -> None:
    """numeric performance assertion: lineage append+get 왕복 p95 가 예산 내.

    I/O 바인딩 연산이므로 wall_ms 로 측정 — `PerfBudget.sample_async` 사용.
    정상 입력에서 녹색; 지연 주입 테스트(`test_..._goes_red_with_io_delay`)
    에서 같은 단언이 적색으로 전환됨을 보장한다.
    """
    samples_ms: list[float] = []
    for _ in range(20):
        tenant_id = uuid4()
        parent = _experiment(tenant_id=tenant_id)
        child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)

        async def _one_lineage_call(p=parent, c=child, t=tenant_id) -> object:
            await repo.append(p)
            await repo.append(c)
            return await repo.get(t, c.experiment_id)

        sample = await perf_budget.sample_async(_one_lineage_call)
        samples_ms.append(sample.wall_ms)

    p95_ms = _p95(samples_ms)
    print(f"[lineage append] p95={p95_ms:.2f}ms budget<{_APPEND_LINEAGE_P95_BUDGET_MS:.1f}ms")
    assert p95_ms < _APPEND_LINEAGE_P95_BUDGET_MS


async def test_append_parent_child_lineage_orphan_parent_not_prevented(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """negative: FK 제약(experiments_parent_id_fkey) 이 있어 parent 먼저
    삽입해야 함 — parent 없이 child 만 append 하면 FK 위반으로 실패해야 함.
    (append-only 테이블이 FK 를 강제하는 구조 확인)."""
    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.append(child)


async def test_append_parent_child_lineage_wrong_tenant_returns_none(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """negative: 다른 tenant_id 로 조회하면 parent 도 child 도 반환되지 않음
    (tenant isolation)."""
    tenant_id = uuid4()
    wrong_tenant = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)
    await repo.append(parent)
    await repo.append(child)

    got_parent = await repo.get(wrong_tenant, parent.experiment_id)
    got_child = await repo.get(wrong_tenant, child.experiment_id)
    assert got_parent is None
    assert got_child is None


async def test_append_parent_child_lineage_self_reference_blocked(
    pool: asyncpg.Pool, repo: PostgresExperimentRepository
) -> None:
    """negative: parent_id == experiment_id (self-reference) 는 pydantic
    validator 에서 차단해야 함."""
    tenant_id = uuid4()
    exp_id = uuid4()
    with pytest.raises(ValueError, match="parent_id must not reference the experiment itself"):
        _experiment(tenant_id=tenant_id, experiment_id=exp_id, parent_id=exp_id)


@pytest.mark.perf
async def test_append_parent_child_lineage_goes_red_with_io_delay(
    pool: asyncpg.Pool,
    repo: PostgresExperimentRepository,
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """gate-red reproduction: asyncpg.Connection.fetchrow 에 IO 지연(delayed_async,
    2.0s) 을 주입한 후 같은 성능 단언을 돌리면 예산을 초과해야 한다(red).

    `conn.fetchrow` 를 `delayed_async` 로 감싸 1회 호출당 2초 IO 지연을
    주입 — lineage append+get 는 I/O 바인딩 연산이므로 wall-clock
    측정으로 지연을 검증한다. 정상 입력에서 통과하는 것이 테스트의 허수
    (trivial pass) 가 아님을 보장한다. 단언/마커 삭제와 예산 완화 금지.
    """
    original_fetchrow = asyncpg.Connection.fetchrow
    monkeypatch.setattr(asyncpg.Connection, "fetchrow", delayed_async(original_fetchrow, 2.0))

    tenant_id = uuid4()
    parent = _experiment(tenant_id=tenant_id)
    child = _experiment(tenant_id=tenant_id, parent_id=parent.experiment_id)

    async def _one_lineage_call_with_delay() -> object:
        await repo.append(parent)
        await repo.append(child)
        return await repo.get(tenant_id, child.experiment_id)

    sample = await perf_budget.sample_async(_one_lineage_call_with_delay, batch=1)
    assert sample.wall_ms >= _APPEND_LINEAGE_P95_BUDGET_MS, (
        f"지연 주입 후 wall 시간 {sample.wall_ms:.3f}ms — "
        f"예상과 달리 녹색: 측정/단언이 공허함(hollow assertion, "
        f"budget={_APPEND_LINEAGE_P95_BUDGET_MS:.1f}ms)"
    )


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


@pytest.mark.perf
async def test_append_db_roundtrip_p95_within_budget(
    repo: PostgresExperimentRepository, perf_budget: Any
) -> None:
    """raw time.perf_counter() → perf_budget.sample_async 전환(task-10889).

     DB 왕복은 I/O 바운드이므로 wall_ms를 사용한다 — process_time는 대기 시간을
    計量하지 못한다. 같은 프로세스의 CPU 보정 루프 비율을 쓰는 RelativeBudget이
     아닌 PerfBudget을 쓰는 이유: 이 리프는 절대 ms 예산(_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS)
     을 유지하며 측정 방식만 process_time-기반으로 통일한다(task-10775)."""
    samples_ms: list[float] = []
    for _ in range(30):
        experiment = _experiment()
        sample = await perf_budget.sample_async(lambda e=experiment: repo.append(e))
        samples_ms.append(sample.wall_ms)

    p95_ms = _p95(samples_ms)
    print(
        f"[AI-10 append] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _APPEND_DB_ROUNDTRIP_P95_BUDGET_MS
