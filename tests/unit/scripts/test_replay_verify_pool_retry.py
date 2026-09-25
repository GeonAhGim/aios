"""FA-15 -- `scripts/replay_verify.py` initial-connect retry (`_create_pool_with_retry`).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15.

esc-ci-replay_verify.json: local Windows CI hit an intermittent reset on the
TCP socket to Postgres (WinError 64 -> asyncpg ConnectionDoesNotExistError)
-- a transient OS-level flake, not a code regression (bisect landed on an
unrelated comment-only commit both times). `_create_pool_with_retry` retries
the initial connect (task-6177). task-6256: the escalation recurred a 4th
time with the traceback back inside `_create_pool_with_retry`, meaning the
old 5-attempt/~5s linear backoff was exhausted before the shared-Postgres
contention (every worktree on this machine terminates connections against
same-named databases via `setup_test_db.py --reset/--drop`'s
`pg_terminate_backend`) cleared -- `_retry_delay` switches to exponential
backoff with a higher attempt ceiling, sized to that documented contention
window instead of the original arbitrary guess. task-6267: the escalation
recurred a 5th time on that exact commit -- `setup_test_db.py --reset`'s
`pg_terminate_backend` -> `DROP DATABASE` -> `CREATE DATABASE` cycle means a
connect attempt can also land inside the drop/create window itself, not just
the terminate, raising `asyncpg.exceptions.InvalidCatalogNameError` /
`CannotConnectNowError` -- neither is an `OSError` nor a
`ConnectionDoesNotExistError`, so the old except clause let them propagate
uncaught on whatever attempt raced that window, discarding the rest of the
budget regardless of its size. `_RETRYABLE_CONNECT_ERRORS` closes that gap.

The post-connect scan/teardown retry (`_verify_with_retry`,
`_close_pool_ignoring_reset`, `_run`) lives in
`test_replay_verify_scan_retry.py` -- split out at task-6714 (see that
file's docstring) once this file's coverage of the terminate()-on-failure
fix crossed the 500-line file-policy warn threshold.

This test group does not touch a real socket -- `asyncpg.create_pool` is
monkeypatched -- so it runs without TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Generator
from typing import Any

import asyncpg
import pytest

from scripts import replay_verify

pytestmark = pytest.mark.asyncio


class _FakePool:
    """`asyncpg.create_pool`의 실제 계약을 흉내낸다: 호출은 동기적으로 `Pool`류
    객체를 돌려주고, 그 객체를 `await`해야 비로소 연결(또는 실패)이 일어난다
    (asyncpg/pool.py `Pool.__await__` -> `_async__init__`). task-6714:
    `_create_pool_with_retry`가 실패한 시도의 `Pool`을 `terminate()`로
    정리하는지(tests/support/test_db_pool_retry.py의 동일 패턴과 대칭) 검증하려면
    평범한 코루틴 함수로는 흉내 낼 수 없다 -- 코루틴 객체엔 `terminate()`가 없다."""

    def __init__(self, outcome: BaseException | None = None) -> None:
        self._outcome = outcome
        self.terminated = False

    def __await__(self) -> Generator[Any, None, _FakePool]:
        async def _run() -> _FakePool:
            if self._outcome is not None:
                raise self._outcome
            return self

        return _run().__await__()

    def terminate(self) -> None:
        self.terminated = True


if sys.platform == "win32":
    # ProactorEventLoop reset only reproduces on win32 -- gated at collection
    # (not via pytest.mark.skipif) so a non-win32 run collects zero tests
    # here instead of reporting a skip.
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
    made_pools: list[_FakePool] = []

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        pool = _FakePool(
            asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
            if attempts < 3
            else None
        )
        made_pools.append(pool)
        return pool

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    pool = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(pool, _FakePool)
    assert attempts == 3
    assert pool is made_pools[-1] and not pool.terminated
    assert all(p.terminated for p in made_pools[:-1]), (
        "실패한 시도의 Pool은 재시도 전에 terminate돼야 한다"
    )


async def test_create_pool_with_retry_propagates_oserror_after_exhausting_attempts(
    monkeypatch,
) -> None:
    """Fail-closed: a reset on every attempt must still raise, not return a
    pool or swallow the error into a false green."""
    attempts = 0
    made_pools: list[_FakePool] = []

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        pool = _FakePool(OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다"))
        made_pools.append(pool)
        return pool

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    with pytest.raises(OSError):
        await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert attempts == replay_verify._POOL_CONNECT_ATTEMPTS
    assert all(p.terminated for p in made_pools), "실패한 시도는 전부 terminate돼야 한다"


async def test_create_pool_with_retry_succeeds_after_drop_create_race(monkeypatch) -> None:
    """task-6267: a connect attempt landing inside `setup_test_db.py
    --reset`'s `DROP DATABASE` -> `CREATE DATABASE` window (not just the
    `pg_terminate_backend` itself) raises `InvalidCatalogNameError`, then
    `CannotConnectNowError` while Postgres is starting the new database back
    up -- both must be retried, not just `ConnectionDoesNotExistError`."""
    attempts = 0

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _FakePool(
                asyncpg.exceptions.InvalidCatalogNameError('database "x" does not exist')
            )
        if attempts == 2:
            return _FakePool(
                asyncpg.exceptions.CannotConnectNowError("the database system is starting up")
            )
        return _FakePool()

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    pool = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(pool, _FakePool)
    assert attempts == 3


async def test_create_pool_with_retry_terminates_failed_attempt_before_retrying(
    monkeypatch,
) -> None:
    """task-6714: mirrors tests/support/test_db_pool_retry.py's identical
    assertion for `create_pool_with_retry` -- the failed attempt's `Pool` must
    be `terminate()`d before the loop retries, and the eventually-successful
    `Pool` returned to the caller must not be touched."""
    attempts = 0
    made_pools: list[_FakePool] = []

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        pool = _FakePool(
            None
            if attempts == 2
            else asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        )
        made_pools.append(pool)
        return pool

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    result = await replay_verify._create_pool_with_retry("postgresql://u:p@localhost/db")

    assert result is made_pools[1]
    assert made_pools[0].terminated, "실패한 첫 시도의 Pool은 재시도 전에 terminate돼야 한다"
    assert not made_pools[1].terminated, "성공한 Pool은 그대로 반환되고 건드리지 않는다"


async def test_create_pool_with_retry_does_not_retry_unrelated_exceptions(monkeypatch) -> None:
    """Only the transient connect-reset shape is retried -- a real
    programming error (e.g. a bad DSN raising ValueError) must surface on the
    first attempt, not be masked behind five retries."""
    attempts = 0

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal attempts
        attempts += 1
        return _FakePool(ValueError("invalid dsn"))

    monkeypatch.setattr(replay_verify.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(replay_verify.asyncio, "sleep", _no_sleep)

    with pytest.raises(ValueError):
        await replay_verify._create_pool_with_retry("not-a-dsn")

    assert attempts == 1


@pytest.mark.perf
async def test_create_pool_with_retry_succeeds_immediately_without_sleeping(monkeypatch) -> None:
    """Perf assertion: the happy path (first attempt succeeds) must not pay
    any backoff delay -- `asyncio.sleep` is only reached on a retry."""

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
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


async def _no_sleep(delay: float) -> None:
    return None
