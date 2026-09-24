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
