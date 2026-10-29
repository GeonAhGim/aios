"""EM-14 `adapters/tca_storage.py` integration tests -- real DB
(`TEST_DATABASE_URL`), against the actual `foundation_audit_event` table.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md#EM-14 (task-5277,
closing task-4794's REJECT of task-4013 for a missing storage adapter).

Mirrors `tests/foundation/integration/ems/test_route_order.py`'s shape
(pool fixture, idempotency-under-concurrency test), since both adapters
share the "insert_or_get is the repository's idempotency boundary"
contract -- this leaf's twist is that the boundary is enforced over a
shared, non-unique-constrained table (`foundation_audit_event`) via an
advisory lock, not a DB unique index, so the concurrency test here is the
adversarial case, not a formality.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ems.adapters.tca_storage import PostgresTcaResultRepository
from src.foundation.ems.contracts.v1 import TcaResult
from tests.support.db import create_pool_with_retry


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await create_pool_with_retry(dsn, min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresTcaResultRepository(pool)


async def _row_count(pool: asyncpg.Pool, parent_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event "
            "WHERE aggregate_type = 'tca_result' AND aggregate_id = $1",
            parent_id,
        )


def _result(**overrides: Decimal) -> TcaResult:
    defaults = dict(
        arrival_bps=Decimal("12.5"),
        vwap_bps=Decimal("-3.2"),
        impact_bps=Decimal("6.1"),
        fees_bps=Decimal("1.4"),
        opportunity_bps=Decimal("5.0"),
    )
    defaults.update(overrides)
    return TcaResult(**defaults)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_insert_or_get_persists_and_round_trips(pool, repo):
    parent_id = uuid4()
    computed_at = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)
    result = _result()

    record = await repo.insert_or_get(
        parent_id=parent_id, revision=1, result=result, computed_at=computed_at
    )

    assert record.parent_id == parent_id
    assert record.revision == 1
    assert record.result == result
    assert record.computed_at == computed_at
    assert await _row_count(pool, parent_id) == 1

    fetched = await repo.get_by_revision(parent_id, 1)
    assert fetched is not None
    assert fetched.result == result

    latest = await repo.get_latest(parent_id)
    assert latest is not None
    assert latest.tca_id == record.tca_id


async def test_get_latest_returns_highest_revision(pool, repo):
    parent_id = uuid4()
    for revision in (1, 2, 3):
        await repo.insert_or_get(
            parent_id=parent_id,
            revision=revision,
            result=_result(arrival_bps=Decimal(revision)),
            computed_at=datetime.now(UTC),
        )

    latest = await repo.get_latest(parent_id)

    assert latest is not None
    assert latest.revision == 3
    assert latest.result.arrival_bps == Decimal(3)


# ---------------------------------------------------------------------------
# negative tests (D2, >= 3)
# ---------------------------------------------------------------------------


async def test_get_by_revision_returns_none_for_unknown_parent_id(pool, repo):
    assert await repo.get_by_revision(uuid4(), 1) is None


async def test_get_latest_returns_none_for_unknown_parent_id(pool, repo):
    assert await repo.get_latest(uuid4()) is None


async def test_get_by_revision_does_not_leak_across_revisions(pool, repo):
    """A stored revision 1 must not answer a lookup for revision 2 of the
    same parent_id -- `(parent_id, revision)` is the full idempotency key,
    not `parent_id` alone."""
    parent_id = uuid4()
    await repo.insert_or_get(
        parent_id=parent_id, revision=1, result=_result(), computed_at=datetime.now(UTC)
    )

    assert await repo.get_by_revision(parent_id, 2) is None


async def test_insert_or_get_does_not_leak_across_parent_ids(pool, repo):
    """Two distinct parent_ids at the same revision must not collide --
    `aggregate_id` (parent_id) is part of the lookup filter, not just
    `aggregate_revision`."""
    revision = 1
    parent_a, parent_b = uuid4(), uuid4()
    result_a = _result(arrival_bps=Decimal("1"))
    result_b = _result(arrival_bps=Decimal("2"))

    now = datetime.now(UTC)
    await repo.insert_or_get(
        parent_id=parent_a, revision=revision, result=result_a, computed_at=now
    )
    await repo.insert_or_get(
        parent_id=parent_b, revision=revision, result=result_b, computed_at=now
    )

    fetched_a = await repo.get_by_revision(parent_a, revision)
    fetched_b = await repo.get_by_revision(parent_b, revision)
    assert fetched_a is not None and fetched_a.result.arrival_bps == Decimal("1")
    assert fetched_b is not None and fetched_b.result.arrival_bps == Decimal("2")


# ---------------------------------------------------------------------------
# idempotency under concurrency (this leaf's adversarial case -- no DB
# unique constraint backs it, only the shared advisory lock)
# ---------------------------------------------------------------------------


async def test_insert_or_get_concurrent_calls_are_idempotent(pool, repo):
    """20 concurrent `insert_or_get` calls for the same `(parent_id,
    revision)` must leave exactly one `foundation_audit_event` row --
    `foundation_audit_event` has no unique index on `(aggregate_type,
    aggregate_id, aggregate_revision, action)`, so this is what actually
    proves the advisory-lock serialization documented in the module
    docstring, not a formality inherited from `route_decisions`."""
    parent_id = uuid4()
    computed_at = datetime.now(UTC)
    result = _result()

    results = await asyncio.gather(
        *(
            repo.insert_or_get(
                parent_id=parent_id, revision=1, result=result, computed_at=computed_at
            )
            for _ in range(20)
        )
    )

    assert await _row_count(pool, parent_id) == 1
    assert len({record.tca_id for record in results}) == 1


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_insert_or_get_propagates_connection_failure_without_partial_write(pool, repo):
    """When the pool itself cannot hand out a connection (e.g. exhausted /
    dropped), `insert_or_get` must propagate rather than swallow the
    failure, and no row is left behind."""
    parent_id = uuid4()

    class _FailingPool:
        def acquire(self):
            raise ConnectionResetError("simulated connection drop")

    repo._pool = _FailingPool()  # failure injection

    with pytest.raises(ConnectionResetError):
        await repo.insert_or_get(
            parent_id=parent_id,
            revision=1,
            result=_result(),
            computed_at=datetime.now(UTC),
        )

    assert await _row_count(pool, parent_id) == 0


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_insert_or_get_perf_budget_sequential_inserts(pool, repo, perf_budget):
    """50 sequential `insert_or_get` calls (distinct parent_ids) each pay
    one advisory-lock acquisition + one existence-check SELECT + one
    insert -- pins an upper bound so a regression that turns this into
    several round trips per call (e.g. losing the single-transaction
    scoping) shows up here instead of only in production latency."""

    async def _batch():
        for _ in range(50):
            await repo.insert_or_get(
                parent_id=uuid4(),
                revision=1,
                result=_result(),
                computed_at=datetime.now(UTC),
            )

    sample = await perf_budget.sample_async(_batch)
    elapsed = sample.wall_ms / 1000

    assert elapsed < 5.0, f"50건 직렬 삽입이 {elapsed:.3f}s 걸림 (예산 5s 초과)"


# ---------------------------------------------------------------------------
# CPU 0-convergence + gate-red reproduction (D2, task-11539 감사 후속)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_insert_or_get_single_call_cpu_nonzero(pool, repo, perf_budget):
    """단일 호출 벽시간이 0ms가 아님을 검증한다(0 수렴 결함 감지).

    `time.process_time()` 기반 CPU 시간 측정은 I/O 대기 중 0ms가 될 수
    있어(네트워크 지연), 벽시간(`time.perf_counter`)으로 측정 버그를
    감지한다. batch=32로 여러 호출을 묶으면 양자화 오차가 줄어 벽시간을
    정상 측정한다. task-11539 감사: 0 수렴이면 batch로 정정.
    """
    sample = await perf_budget.sample_async(
        lambda: repo.insert_or_get(
            parent_id=uuid4(),
            revision=1,
            result=_result(),
            computed_at=datetime.now(UTC),
        ),
        batch=32,
    )
    assert sample.wall_ms > 0, (
        f"단일 호출 벽시간이 0ms (batch=32 기준) — 측정 버그: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_insert_or_get_perf_budget_goes_red_with_sleep_injection(
    pool, repo, perf_budget, monkeypatch
):
    """gate-red reproduction: sleep 주입으로 지연을 인위적으로 늘리면
    기존 녹색 성능 단언이 적색으로 떨어져야 한다.

    `insert_or_get` 내부의 DB 왕복 전에 `asyncio.sleep(0.05)`를 주입해
    같은 측정/단언을 재사용한다. 예산 10배 지연이므로 50건 기준
    wall_ms 예산을 초과해야 한다. 단언/마커 삭제와 예산 완화 금지."""
    import asyncio

    # Wrap the repo's insert_or_get to inject sleep before each DB call
    original_insert_or_get = repo.insert_or_get

    async def slow_insert_or_get(*args, **kwargs):
        await asyncio.sleep(0.1)  # 100ms delay per call → ~10× 실제 DB 왕복
        return await original_insert_or_get(*args, **kwargs)

    repo.insert_or_get = slow_insert_or_get

    async def _batch():
        for _ in range(50):
            await repo.insert_or_get(
                parent_id=uuid4(),
                revision=1,
                result=_result(),
                computed_at=datetime.now(UTC),
            )

    sample = await perf_budget.sample_async(_batch)
    elapsed = sample.wall_ms / 1000

    assert elapsed >= 5.0, (
        f"sleep 주입 후 50건 직렬 삽입이 {elapsed:.3f}s — "
        f"예산 5s 초과 단언이 실패: 측정/단언이 공허함(hollow assertion)"
    )
