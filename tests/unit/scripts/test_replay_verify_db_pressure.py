"""FA-15 -- `scripts/replay_verify_db_pressure.py` (`await_db_capacity`).

esc-ci-replay_verify.json (13th+ recurrence, task-6754): every recurrence carries
`"mode": "full"`, i.e. it comes through pm/ci_recheck.py's direct subprocess call
to `scripts/replay_verify.py`, a path pm/local_ci.py's task-6743 connection-pressure
pre-check does not cover. This module duplicates that gate inside the script itself
(see its own docstring); this file covers it in isolation with a fake probe/sleep,
no real socket or TEST_DATABASE_URL needed.
"""

from __future__ import annotations

import time

import pytest

from scripts import replay_verify_db_pressure as pressure

pytestmark = pytest.mark.asyncio


def _make_probe(sequence: list[tuple[int, int] | None]):
    calls = 0

    async def _probe(dsn: str) -> tuple[int, int] | None:
        nonlocal calls
        result = sequence[min(calls, len(sequence) - 1)]
        calls += 1
        return result

    return _probe, lambda: calls


def _make_sleep():
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    return _sleep, delays


@pytest.mark.perf
async def test_await_db_capacity_returns_immediately_under_threshold() -> None:
    """Perf assertion: no pressure on the first probe means zero sleeps and the
    call returns without paying any of the backoff schedule."""
    probe, calls = _make_probe([(10, 100)])
    sleep, delays = _make_sleep()

    started = time.perf_counter()
    await pressure.await_db_capacity("postgresql://u:p@localhost/db", sleep=sleep, probe=probe)
    elapsed = time.perf_counter() - started

    assert calls() == 1
    assert delays == []
    assert elapsed < 0.05


async def test_await_db_capacity_waits_out_transient_pressure() -> None:
    """Negative test: pressure above threshold on the first probe, cleared by the
    second -- the gate must wait (not fail, not proceed immediately)."""
    probe, calls = _make_probe([(80, 100), (10, 100)])
    sleep, delays = _make_sleep()

    await pressure.await_db_capacity("postgresql://u:p@localhost/db", sleep=sleep, probe=probe)

    assert calls() == 2
    assert delays == [pressure.DB_PRESSURE_BACKOFF_SEC[0]]


async def test_await_db_capacity_exhausts_budget_and_proceeds_anyway() -> None:
    """Negative test / fail-open: pressure that never clears must not block
    forever -- the fixed retry budget (DECISION_GUIDELINES B-2: never
    unbounded) runs out and the function returns regardless."""
    probe, calls = _make_probe([(90, 100)])
    sleep, delays = _make_sleep()

    await pressure.await_db_capacity("postgresql://u:p@localhost/db", sleep=sleep, probe=probe)

    assert calls() == pressure.DB_PRESSURE_MAX_RETRIES + 1
    assert delays == list(pressure.DB_PRESSURE_BACKOFF_SEC)


async def test_await_db_capacity_treats_unreadable_probe_as_proceed() -> None:
    """Negative test: a probe failure (network blip, DB mid-recreate) must be
    treated as unknown pressure, not as pressure itself -- it must not block a
    legitimate run."""
    probe, calls = _make_probe([None])
    sleep, delays = _make_sleep()

    await pressure.await_db_capacity("postgresql://u:p@localhost/db", sleep=sleep, probe=probe)

    assert calls() == 1
    assert delays == []


async def test_await_db_capacity_treats_zero_max_connections_as_proceed() -> None:
    """Failure-injection test: a probe returning `max_conn=0` (an impossible
    but not-unseen shape if `pg_settings` is misread) must not raise
    `ZeroDivisionError` -- the `max_conn <= 0` guard treats it as proceed."""
    probe, calls = _make_probe([(5, 0)])
    sleep, delays = _make_sleep()

    await pressure.await_db_capacity("postgresql://u:p@localhost/db", sleep=sleep, probe=probe)

    assert calls() == 1
    assert delays == []


async def test_stagger_startup_jitters_within_jitter_max_bound(monkeypatch) -> None:
    """task-7648 (esc-ci-replay_verify.json, 14th+ recurrence): mirrors
    `replay_verify._sleep_before_retry`'s decorrelation proof (task-6627), but
    for the *first* connect attempt -- pm/local_ci.py spawns one
    `replay_verify.py` subprocess per xdist worker DB at effectively the same
    instant, so a deterministic (non-random) startup delay would just move
    the thundering herd to a fixed offset instead of spreading it. Asserts
    `stagger_startup` draws `random.uniform(0, _STARTUP_JITTER_MAX_SEC)`, not
    the bound itself."""
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr(pressure.random, "uniform", lambda lo, hi: lo + (hi - lo) * 0.25)

    await pressure.stagger_startup(sleep=_capture_sleep)

    assert captured == [pressure._STARTUP_JITTER_MAX_SEC * 0.25]


async def test_stagger_startup_never_exceeds_jitter_max_bound() -> None:
    """Negative test: across many real draws, the jittered startup delay must
    never leave `[0, _STARTUP_JITTER_MAX_SEC]` -- a broken jitter call could
    silently turn this into an unbounded (or negative) sleep."""
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    for _ in range(50):
        await pressure.stagger_startup(sleep=_capture_sleep)

    assert all(0.0 <= delay <= pressure._STARTUP_JITTER_MAX_SEC for delay in captured)


async def test_stagger_startup_always_sleeps_exactly_once() -> None:
    """Failure-injection-adjacent negative test: `stagger_startup` must call
    `sleep` exactly once per invocation -- calling it zero times would silently
    drop the decorrelation this function exists to provide, and more than once
    would double-pay the jitter budget."""
    calls = 0

    async def _counting_sleep(delay: float) -> None:
        nonlocal calls
        calls += 1

    await pressure.stagger_startup(sleep=_counting_sleep)

    assert calls == 1


async def test_connection_pressure_returns_none_on_connect_failure(monkeypatch) -> None:
    """Failure-injection test: `connection_pressure` itself must swallow a
    connect-time OSError/PostgresError into `None`, not propagate it -- a
    propagating exception here would crash `replay_verify.py` on the pressure
    probe alone, before it ever reaches the actual verification."""
    import asyncpg

    async def _boom(*, dsn: str, timeout: float) -> None:
        raise OSError(64, "network name no longer available")

    monkeypatch.setattr(asyncpg, "connect", _boom)

    result = await pressure.connection_pressure("postgresql://u:p@localhost/db")

    assert result is None


def test_target_database_name_strips_leading_slash() -> None:
    assert pressure.target_database_name("postgresql://u:p@localhost/aios_test_ci") == (
        "aios_test_ci"
    )


def test_maintenance_dsn_swaps_database_to_postgres() -> None:
    """`maintenance_dsn` must point at the stable `postgres` database, not the
    target database that may itself be mid-`DROP`/`CREATE` -- probing the
    target directly is exactly the gap this module's `reset_lock_held` exists
    to avoid."""
    assert pressure.maintenance_dsn("postgresql://u:p@localhost:5432/aios_test_ci") == (
        "postgresql://u:p@localhost:5432/postgres"
    )


def _make_reset_probe(sequence: list[bool | None]):
    calls: list[tuple[str, str]] = []

    async def _probe(dsn: str, database: str) -> bool | None:
        result = sequence[min(len(calls), len(sequence) - 1)]
        calls.append((dsn, database))
        return result

    return _probe, calls


async def test_await_reset_lock_clear_returns_immediately_when_free() -> None:
    """Perf assertion: the lock already free on the first probe means zero
    sleeps and the call returns without paying any of the backoff schedule."""
    probe, calls = _make_reset_probe([False])
    sleep, delays = _make_sleep()

    started = time.perf_counter()
    await pressure.await_reset_lock_clear(
        "postgresql://u:p@localhost/aios_test_ci", sleep=sleep, probe=probe
    )
    elapsed = time.perf_counter() - started

    assert len(calls) == 1
    assert calls[0] == ("postgresql://u:p@localhost/postgres", "aios_test_ci")
    assert delays == []
    assert elapsed < 0.05


async def test_await_reset_lock_clear_waits_out_transient_reset() -> None:
    """Negative test: the lock held on the first probe, cleared by the second
    -- the gate must wait (not fail, not proceed immediately)."""
    probe, calls = _make_reset_probe([True, False])
    sleep, delays = _make_sleep()

    await pressure.await_reset_lock_clear(
        "postgresql://u:p@localhost/aios_test_ci", sleep=sleep, probe=probe
    )

    assert len(calls) == 2
    assert delays == [pressure.RESET_LOCK_BACKOFF_SEC[0]]


async def test_await_reset_lock_clear_exhausts_budget_and_proceeds_anyway() -> None:
    """Negative test / fail-open: a lock that never clears must not block
    forever -- the fixed retry budget (DECISION_GUIDELINES B-2: never
    unbounded) runs out and the function returns regardless."""
    probe, calls = _make_reset_probe([True])
    sleep, delays = _make_sleep()

    await pressure.await_reset_lock_clear(
        "postgresql://u:p@localhost/aios_test_ci", sleep=sleep, probe=probe
    )

    assert len(calls) == pressure.RESET_LOCK_MAX_RETRIES + 1
    assert delays == list(pressure.RESET_LOCK_BACKOFF_SEC)


async def test_await_reset_lock_clear_treats_unreadable_probe_as_proceed() -> None:
    """Negative test: a probe failure (network blip, maintenance DB
    unreachable) must be treated as unknown, not as held -- it must not block
    a legitimate run."""
    probe, calls = _make_reset_probe([None])
    sleep, delays = _make_sleep()

    await pressure.await_reset_lock_clear(
        "postgresql://u:p@localhost/aios_test_ci", sleep=sleep, probe=probe
    )

    assert len(calls) == 1
    assert delays == []


async def test_reset_lock_held_returns_none_on_connect_failure(monkeypatch) -> None:
    """Failure-injection test: `reset_lock_held` itself must swallow a
    connect-time OSError/PostgresError into `None`, not propagate it -- a
    propagating exception here would crash `replay_verify.py` on the reset
    probe alone, before it ever reaches the actual verification."""
    import asyncpg

    async def _boom(*, dsn: str, timeout: float) -> None:
        raise OSError(64, "network name no longer available")

    monkeypatch.setattr(asyncpg, "connect", _boom)

    result = await pressure.reset_lock_held("postgresql://u:p@localhost/postgres", "aios_test_ci")

    assert result is None


async def test_reset_lock_held_releases_lock_when_acquired(monkeypatch) -> None:
    """`reset_lock_held` must release the advisory lock it just took to probe
    -- a probe that leaks a held lock would itself become the contention the
    next real `setup_test_db.py --reset` blocks on."""
    import asyncpg

    executed: list[tuple[str, tuple]] = []

    class _FakeConn:
        async def fetchval(self, query: str, *args):
            executed.append((query, args))
            return True

        async def execute(self, query: str, *args):
            executed.append((query, args))

        async def close(self):
            pass

    async def _fake_connect(*, dsn: str, timeout: float):
        return _FakeConn()

    monkeypatch.setattr(asyncpg, "connect", _fake_connect)

    result = await pressure.reset_lock_held("postgresql://u:p@localhost/postgres", "aios_test_ci")

    assert result is False
    assert any("pg_try_advisory_lock" in q for q, _ in executed)
    assert any("pg_advisory_unlock" in q for q, _ in executed)
