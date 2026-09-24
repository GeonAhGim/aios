"""FA-15 -- `scripts/replay_verify.py` connection-reset retry.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15.

esc-ci-replay_verify.json: local Windows CI hit an intermittent reset on the
TCP socket to Postgres (WinError 64 -> asyncpg ConnectionDoesNotExistError)
-- a transient OS-level flake, not a code regression (bisect landed on an
unrelated comment-only commit both times). `_create_pool_with_retry` retries
the initial connect (task-6177); `_verify_with_retry` retries the scan
itself for the same reset landing after the pool is already up, mid
read-only transaction (task-6213). `_close_pool_ignoring_reset` absorbs the
same reset shape hitting an idle pooled connection during `pool.close()`
teardown, after `verify()` has already produced its (fail-closed) result
(task-6236) -- neither `_run`'s `finally` nor a real bug in the scan itself
should have its outcome replaced by a teardown-only socket error. task-6256:
the escalation recurred a 4th time with the traceback back inside
`_create_pool_with_retry`, meaning the old 5-attempt/~5s linear backoff was
exhausted before the shared-Postgres contention (every worktree on this
machine terminates connections against same-named databases via
`setup_test_db.py --reset/--drop`'s `pg_terminate_backend`) cleared --
`_retry_delay` switches both retry loops to exponential backoff with a
higher attempt ceiling, sized to that documented contention window instead
of the original arbitrary guess. task-6267: the escalation recurred a 5th time
on that exact commit -- `setup_test_db.py --reset`'s `pg_terminate_backend`
-> `DROP DATABASE` -> `CREATE DATABASE` cycle means a connect attempt can
also land inside the drop/create window itself, not just the terminate,
raising `asyncpg.exceptions.InvalidCatalogNameError` /
`CannotConnectNowError` -- neither is an `OSError` nor a
`ConnectionDoesNotExistError`, so the old except clause let them propagate
uncaught on whatever attempt raced that window, discarding the rest of the
budget regardless of its size. `_RETRYABLE_CONNECT_ERRORS` closes that gap.
Neither test group touches a real socket -- `asyncpg.create_pool` /
`replay_verify.verify` / `pool.close` are monkeypatched -- so they run
without TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

import asyncpg
import pytest

from scripts import replay_verify
from src.core.eventstore import replay

pytestmark = pytest.mark.asyncio


class _FakePool:
    pass


@pytest.mark.skipif(
    sys.platform != "win32", reason="ProactorEventLoop reset only reproduces on win32"
)
def test_module_import_sets_windows_selector_event_loop_policy() -> None:
    """task-6522 root cause (esc-ci-replay_verify.json, 6th recurrence): every
    traceback landed in `asyncio/windows_events.py`'s `finish_recv` --
    `ProactorEventLoop`-only IOCP internals. Importing `scripts.replay_verify`
    on win32 must switch the process to `WindowsSelectorEventLoopPolicy`
    (which drives sockets via `select`/`poll` and has no IOCP
    reset-propagation path), not just retry around the reset -- a regression
    that silently reverted this policy switch would reintroduce the whole
    class of flake with none of the retry tests above (they monkeypatch
    `asyncpg.create_pool`/`verify` directly and never touch a real socket)
    catching it."""
    assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


def test_retry_delay_grows_exponentially_and_caps() -> None:
    """task-6256: the schedule must actually widen the contention window the
    old linear backoff (`0.5*(attempt+1)`, ~5s total over 5 attempts) proved
    too short for, not just add more attempts at the same short cadence."""
    assert [replay_verify._retry_delay(a) for a in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]


def test_retry_delay_total_budget_exceeds_prior_5s_window() -> None:
    """Negative test for the regression itself: summed over
    `_POOL_CONNECT_ATTEMPTS` retries, the new schedule must clear the ~5s
    total the old linear backoff gave the flake in esc-ci-replay_verify.json
    before all attempts were exhausted."""
    total = sum(
        replay_verify._retry_delay(a) for a in range(replay_verify._POOL_CONNECT_ATTEMPTS - 1)
    )
    assert total > 5.0


async def test_create_pool_with_retry_succeeds_after_transient_reset(monkeypatch) -> None:
    """First two attempts raise the exact esc-ci-replay_verify.json exception
    shape (OSError-derived ConnectionResetError surfacing through asyncpg as
    ConnectionDoesNotExistError); the third succeeds -- the flake must not
    fail the whole CI step."""
    attempts = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return _FakePool()

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    pool = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(pool, _FakePool)
    assert attempts == 3


async def test_create_pool_with_retry_propagates_oserror_after_exhausting_attempts(
    monkeypatch,
) -> None:
    """Fail-closed: a reset on every attempt must still raise, not return a
    pool or swallow the error into a false green."""
    attempts = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    with pytest.raises(OSError):
        await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert attempts == replay_verify._POOL_CONNECT_ATTEMPTS


async def test_create_pool_with_retry_succeeds_after_drop_create_race(monkeypatch) -> None:
    """task-6267: a connect attempt landing inside `setup_test_db.py
    --reset`'s `DROP DATABASE` -> `CREATE DATABASE` window (not just the
    `pg_terminate_backend` itself) raises `InvalidCatalogNameError`, then
    `CannotConnectNowError` while Postgres is starting the new database back
    up -- both must be retried, not just `ConnectionDoesNotExistError`."""
    attempts = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncpg.exceptions.InvalidCatalogNameError('database "x" does not exist')
        if attempts == 2:
            raise asyncpg.exceptions.CannotConnectNowError("the database system is starting up")
        return _FakePool()

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    pool = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(pool, _FakePool)
    assert attempts == 3


async def test_create_pool_with_retry_does_not_retry_unrelated_exceptions(monkeypatch) -> None:
    """Only the transient connect-reset shape is retried -- a real
    programming error (e.g. a bad DSN raising ValueError) must surface on the
    first attempt, not be masked behind five retries."""
    attempts = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid dsn")

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    with pytest.raises(ValueError):
        await replay_verify._create_pool_with_retry("not-a-dsn")

    assert attempts == 1


async def test_create_pool_with_retry_succeeds_immediately_without_sleeping(monkeypatch) -> None:
    """Perf assertion: the happy path (first attempt succeeds) must not pay
    any backoff delay -- `asyncio.sleep` is only reached on a retry."""

    async def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        return _FakePool()

    slept = False

    async def _tracking_sleep(delay: float) -> None:
        nonlocal slept
        slept = True

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _tracking_sleep)

    started = time.perf_counter()
    pool = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")
    elapsed = time.perf_counter() - started

    assert isinstance(pool, _FakePool)
    assert slept is False
    assert elapsed < 0.05, f"first-attempt success took {elapsed:.3f}s, expected no backoff delay"


async def test_verify_with_retry_succeeds_after_mid_scan_reset(monkeypatch) -> None:
    """task-6213: the reset lands *after* the pool connected, inside
    `verify()`'s read-only scan -- the exact
    `asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in
    the middle of operation")` shape from the recurrence. The retry must
    re-run the whole (side-effect-free) scan and succeed on a later
    attempt."""
    attempts = 0
    sentinel_report = replay.ReplayReport(
        streams_checked=87, combined_digest="deadbeef", mismatches=()
    )

    async def _fake_verify(pool: object, *, as_of: object, hours: object) -> replay.ReplayReport:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return sentinel_report

    monkeypatch.setattr(replay_verify, "verify", _fake_verify)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    report = await replay_verify._verify_with_retry(
        object(), as_of=datetime.now(timezone.utc), hours=24
    )

    assert report is sentinel_report
    assert attempts == 3


async def test_verify_with_retry_succeeds_after_drop_create_race(monkeypatch) -> None:
    """task-6284: `pool.acquire()` inside `verify()`'s scan can dial a new
    physical connection (pool growth / replacing a discarded one) that lands
    inside `setup_test_db.py --reset`'s `DROP DATABASE` -> `CREATE DATABASE`
    window, raising `InvalidCatalogNameError` then `CannotConnectNowError`
    -- the exact shape `_create_pool_with_retry` already retries for the
    initial connect. `_verify_with_retry` must retry the same shapes, not
    just `ConnectionDoesNotExistError`."""
    attempts = 0

    async def _fake_verify(pool: object, *, as_of: object, hours: object) -> replay.ReplayReport:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncpg.exceptions.InvalidCatalogNameError('database "x" does not exist')
        if attempts == 2:
            raise asyncpg.exceptions.CannotConnectNowError("the database system is starting up")
        return replay.ReplayReport(streams_checked=3, combined_digest="cafe", mismatches=())

    monkeypatch.setattr(replay_verify, "verify", _fake_verify)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    report = await replay_verify._verify_with_retry(
        object(), as_of=datetime.now(timezone.utc), hours=24
    )

    assert report.streams_checked == 3
    assert attempts == 3


async def test_verify_with_retry_propagates_after_exhausting_attempts(monkeypatch) -> None:
    """Fail-closed: a reset on every attempt must still raise, not report a
    false green."""
    attempts = 0

    async def _fake_verify(pool: object, *, as_of: object, hours: object) -> replay.ReplayReport:
        nonlocal attempts
        attempts += 1
        raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    monkeypatch.setattr(replay_verify, "verify", _fake_verify)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    with pytest.raises(OSError):
        await replay_verify._verify_with_retry(
            object(), as_of=datetime.now(timezone.utc), hours=24
        )

    assert attempts == replay_verify._POOL_CONNECT_ATTEMPTS


async def test_verify_with_retry_does_not_retry_a_real_mismatch_report(monkeypatch) -> None:
    """A genuine replay mismatch is a return value (`report.ok is False`),
    not an exception -- it must surface on the first attempt, not be masked
    behind retries meant only for connection resets."""
    attempts = 0
    mismatch_report = replay.ReplayReport(
        streams_checked=1,
        combined_digest="mismatch",
        mismatches=(
            replay.StreamDiff(domain="orders", key="x", replayed_digest="a", actual_digest="b"),
        ),
    )

    async def _fake_verify(pool: object, *, as_of: object, hours: object) -> replay.ReplayReport:
        nonlocal attempts
        attempts += 1
        return mismatch_report

    monkeypatch.setattr(replay_verify, "verify", _fake_verify)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    report = await replay_verify._verify_with_retry(
        object(), as_of=datetime.now(timezone.utc), hours=24
    )

    assert report is mismatch_report
    assert attempts == 1


async def test_close_pool_ignoring_reset_swallows_connection_reset() -> None:
    """task-6236: a reset hitting an idle pooled connection during teardown
    must not raise -- `verify()`'s result is already final by the time
    `_run`'s `finally` calls this."""

    class _ResetOnClosePool:
        async def close(self) -> None:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )

    await replay_verify._close_pool_ignoring_reset(_ResetOnClosePool())  # must not raise


async def test_close_pool_ignoring_reset_swallows_drop_create_race() -> None:
    """task-6302: `pool.close()` can itself dial out to close pooled
    connections and land inside the same `setup_test_db.py --reset`
    `DROP DATABASE` -> `CREATE DATABASE` window `_create_pool_with_retry`
    and `_verify_with_retry` already treat as transient
    (`InvalidCatalogNameError` / `CannotConnectNowError`). Unlike those two
    call sites there is no retry here -- the only correct behavior is to
    swallow it, matching `_RETRYABLE_CONNECT_ERRORS` exactly, since `report`
    is already computed by the time this runs and an uncaught exception here
    would replace an already-successful result with a false CI failure."""

    class _InvalidCatalogOnClosePool:
        async def close(self) -> None:
            raise asyncpg.exceptions.InvalidCatalogNameError('database "x" does not exist')

    class _CannotConnectNowOnClosePool:
        async def close(self) -> None:
            raise asyncpg.exceptions.CannotConnectNowError("the database system is starting up")

    await replay_verify._close_pool_ignoring_reset(_InvalidCatalogOnClosePool())  # must not raise
    await replay_verify._close_pool_ignoring_reset(_CannotConnectNowOnClosePool())  # must not raise


async def test_close_pool_ignoring_reset_propagates_unrelated_exceptions() -> None:
    """Only the transient connection-reset shape is swallowed -- a real bug
    in `pool.close()` must still surface, not be silently hidden."""

    class _BrokenPool:
        async def close(self) -> None:
            raise ValueError("not a connection reset")

    with pytest.raises(ValueError):
        await replay_verify._close_pool_ignoring_reset(_BrokenPool())


async def test_run_propagates_real_failure_even_if_close_also_resets(monkeypatch) -> None:
    """A genuine fail-closed exception from the scan (reset on every retry
    attempt, i.e. not absorbed) must still propagate as the process's
    failure even when `pool.close()` in the `finally` also hits a reset --
    the close-time reset must not mask or replace it."""

    class _ResetOnClosePool:
        async def close(self) -> None:
            raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    async def _fake_create_pool_with_retry(dsn: str) -> _ResetOnClosePool:
        return _ResetOnClosePool()

    async def _fake_verify_with_retry(
        pool: object, *, as_of: object, hours: object
    ) -> replay.ReplayReport:
        raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    monkeypatch.setattr(replay_verify, "_create_pool_with_retry", _fake_create_pool_with_retry)
    monkeypatch.setattr(replay_verify, "_verify_with_retry", _fake_verify_with_retry)

    with pytest.raises(OSError):
        await replay_verify._run(hours=24, as_of=datetime.now(timezone.utc))


async def _no_sleep(delay: float) -> None:
    return None
