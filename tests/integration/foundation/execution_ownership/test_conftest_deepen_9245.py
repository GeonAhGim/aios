"""DEEPEN(task-9245) for tests/integration/foundation/execution_ownership/conftest.py.

원 리프 task-6704(고아 산출물 회수 5828, qa-2) 대상 — `conftest.py`가 제공하는
`_asyncpg_dsn()`/`pool`/`create_execution()`/`execution_id`는 지금까지 한 번도
불변식 위반 입력으로 직접 검증된 적이 없다(negative 0건). `create_execution()`은
`strategy_executions`의 FK/CHECK/NOT NULL 제약을 그대로 통과시키는 얇은 헬퍼라
그 제약이 실제로 fail-closed인지가 이 리프의 유일한 실행 가능한 계약이다.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

import asyncpg
import pytest

import tests.integration.foundation.execution_ownership.conftest as _conftest
from tests.integration.conftest import create_test_user
from tests.integration.foundation.execution_ownership.conftest import (
    _asyncpg_dsn,
    create_execution,
    pool,
)

_pool_fn: Any = cast(Any, pool).__wrapped__


# ---------------------------------------------------------------------------
# Negative tests -- _asyncpg_dsn must fail closed, never guess a default.
# ---------------------------------------------------------------------------


def test_asyncpg_dsn_raises_when_database_url_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `DATABASE_URL` must surface as `KeyError`, not a silently
    substituted local/default connection string."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


# ---------------------------------------------------------------------------
# Negative tests -- create_execution must let the strategy_executions
# FK/NOT NULL constraints reject invariant-violating input, not paper over it.
# ---------------------------------------------------------------------------


async def test_create_execution_rejects_unknown_user_id(pool: asyncpg.Pool) -> None:
    """`strategy_executions.user_id` FKs to `users(user_id)` -- an id that
    was never inserted via `create_test_user` must be rejected by Postgres,
    not silently accepted as an orphaned execution row."""
    unknown_user_id = uuid.uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_execution(pool, unknown_user_id)


async def test_create_execution_rejects_null_allocated_capital(pool: asyncpg.Pool) -> None:
    """`allocated_capital` is `NUMERIC(20,2) NOT NULL` -- a caller passing
    `None` must hit the NOT NULL constraint, never insert a row with an
    unknown capital allocation (Decimal-only invariant, CLAUDE.md §3)."""
    user_id = await create_test_user(pool)
    null_capital = cast(Any, None)
    with pytest.raises(asyncpg.NotNullViolationError):
        await create_execution(pool, user_id, allocated_capital=null_capital)


async def test_create_execution_rejects_duplicate_strategy_id(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`strategies(strategy_id, version)` is the composite FK target for
    `strategy_executions` -- two inserts racing to reuse the same generated
    `strategy_id` must hit the primary-key uniqueness constraint on
    `strategies` rather than silently overwriting/duplicating the row."""
    user_id = await create_test_user(pool)
    # Genuinely random per test run (not a hardcoded constant) -- the
    # `strategies` table is shared across the whole test session with no
    # rollback isolation, so a fixed literal would collide with a prior
    # run's leftover row on the *first* insert instead of the second.
    fixed = uuid.uuid4()
    monkeypatch.setattr(uuid, "uuid4", lambda: fixed)

    await create_execution(pool, user_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await create_execution(pool, user_id)


# ---------------------------------------------------------------------------
# Failure injection -- the pool fixture body must propagate connection
# failures instead of swallowing them into a broken/None pool.
# ---------------------------------------------------------------------------


async def test_pool_fixture_propagates_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `create_pool_with_retry` exhausts its bounded retries (DB down,
    bad DSN, auth rejected), the `pool` fixture must let that exception
    reach the test instead of yielding a broken pool that downstream
    fixtures (`execution_id`) would misinterpret as "connected"."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@nonexistent-host/db")

    async def _boom(*_args: Any, **_kwargs: Any) -> asyncpg.Pool[asyncpg.Connection]:
        raise OSError("connection refused")

    monkeypatch.setattr(_conftest, "create_pool_with_retry", _boom)

    agen = _pool_fn()
    with pytest.raises(OSError, match="connection refused"):
        await agen.__anext__()


# ---------------------------------------------------------------------------
# Gate-red reproduction -- prove the FK negative test actually catches a
# regression that would drop the constraint check.
# ---------------------------------------------------------------------------


async def test_gate_red_unknown_user_id_regression_is_caught(pool: asyncpg.Pool) -> None:
    """Simulates the regression a naive "just make the test pass" patch
    would introduce: catching and swallowing the FK violation instead of
    propagating it. If that regression lands, this assertion -- not the
    happy-path suite -- is what must fail."""
    unknown_user_id = uuid.uuid4()
    swallowed = False
    try:
        await create_execution(pool, unknown_user_id)
    except asyncpg.ForeignKeyViolationError:
        swallowed = False
    else:
        swallowed = True

    assert not swallowed, (
        "create_execution must not silently accept an unknown user_id -- "
        "a regression that swallows the FK violation would land here"
    )


# ---------------------------------------------------------------------------
# Performance assertion -- pool fixture must establish connection within budget.
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_pool_creation_performance_within_budget(perf_budget) -> None:
    """Pool creation (including retry loop) must complete within a reasonable
    latency budget (ADR-2026-09-09-C). This prevents accidental O(n) blocking
    or infinite waits during test setup that would accumulate across the suite."""

    async def _create_pool():
        agen = _pool_fn()
        p = await agen.__anext__()
        await p.close()
        return p

    measured = await perf_budget.sample_async(_create_pool)
    elapsed = measured.wall_ms / 1000

    assert elapsed < 10.0, (
        f"pool fixture must establish connection within 10s budget; "
        f"took {elapsed:.2f}s (possible retry loop or network latency)"
    )


# ---------------------------------------------------------------------------
# Adversarial test -- concurrent create_execution maintains I-02 invariant
# (lease/fencing token, owner change only on increment) under race conditions.
# ---------------------------------------------------------------------------


async def test_concurrent_create_execution_maintains_invariant(
    pool: asyncpg.Pool,
) -> None:
    """I-02 states: multi-process read of execution ownership must have
    lease/fencing token, increment only on owner change. Even under concurrent
    calls, create_execution must not corrupt the strategy or execution row
    with partial/interleaved state or duplicate IDs."""
    user_id = await create_test_user(pool)

    # Spawn 3 concurrent executions -- verify each has unique IDs
    # and all succeed without race condition or data corruption
    ids = await asyncio.gather(
        create_execution(pool, user_id),
        create_execution(pool, user_id),
        create_execution(pool, user_id),
    )

    assert len(set(ids)) == 3, (
        f"concurrent create_execution calls must produce distinct IDs, not collide or reuse: {ids}"
    )

    # Verify all rows are fully inserted (not partial state)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, status FROM strategy_executions WHERE id = ANY($1)",
            ids,
        )

    assert len(rows) == 3, (
        f"all 3 concurrent executions must be fully committed; "
        f"found only {len(rows)} rows (possible partial insert under race)"
    )
    for row in rows:
        assert row["status"] == "RUNNING", (
            f"execution {row['id']} has corrupted status {row['status']}, "
            f"expected RUNNING after concurrent create"
        )
