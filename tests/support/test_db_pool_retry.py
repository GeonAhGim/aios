"""tests/support/db.py `create_pool_with_retry` — 재시도·지터·누수 정정.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.4/§9 PLT-36.
`tests/support/test_db.py`에서 분리했다 — 대상 관심사(연결 풀 재시도 수명주기)가
DB URL/이름 파싱·CLI 게이트 재현과 다른 축이라 별도 파일로 나누는 편이
`ADR-2026-09-10-C` §7 취지(경계별 분리)에 맞는다.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from typing import Any

import asyncpg
import pytest

from tests.support.db import (
    _admin_connect_with_retry,
    _pool_retry_delay,
    _sleep_before_pool_retry,
    create_pool_with_retry,
)


class _FakePool:
    """`asyncpg.create_pool`의 실제 계약을 흉내낸다: 호출은 동기적으로 `Pool`류
    객체를 돌려주고, 그 객체를 `await`해야 비로소 연결(또는 실패)이 일어난다
    (asyncpg/pool.py `Pool.__await__` -> `_async__init__`). `create_pool_with_retry`가
    실패한 시도의 `Pool`을 `terminate()`로 정리하는지(누수 회귀 방지, 아래
    `test_create_pool_with_retry_terminates_failed_attempt_before_retrying`)
    검증하려면 평범한 코루틴 함수로는 흉내 낼 수 없다 -- 코루틴 객체엔
    `terminate()`가 없다."""

    def __init__(self, outcome: BaseException | None) -> None:
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


@pytest.mark.asyncio
async def test_create_pool_with_retry_retries_transient_reset_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """esc-ci-pytest.json/task-6235: a transient WinError 64 / asyncpg
    ConnectionDoesNotExistError on the first attempt is absorbed -- the second
    attempt's successful pool is returned, not the exception."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _FakePool(
                asyncpg.exceptions.ConnectionDoesNotExistError(
                    "connection was closed in the middle of operation"
                )
            )
        return _FakePool(None)

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(result, _FakePool) and not result.terminated
    assert calls == 2


@pytest.mark.asyncio
async def test_create_pool_with_retry_terminates_failed_attempt_before_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asyncpg.create_pool(...)` returns a `Pool` synchronously; connecting
    happens on `await`. If a later holder's connect fails mid-init, the first
    holder's connection is already live (asyncpg/pool.py `_initialize` connects
    it directly before gathering the rest) with `self._initialized` still set
    True in `_async__init__`'s `finally` -- so that live connection is only
    reachable through the failed `Pool` object. Discarding it without
    `terminate()` on retry leaks a live Postgres connection per failed attempt,
    which only makes the "too many connections" family of transient resets
    this retry loop exists to absorb *more* likely, not less. This asserts the
    failed attempt's `Pool` is terminated before the loop retries -- the
    successful attempt's `Pool` (returned to the caller) must NOT be touched."""
    db_module = sys.modules["tests.support.db"]
    calls = 0
    made_pools: list[_FakePool] = []

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal calls
        calls += 1
        pool = _FakePool(
            None
            if calls == 2
            else asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        )
        made_pools.append(pool)
        return pool

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert result is made_pools[1]
    assert made_pools[0].terminated, "실패한 첫 시도의 Pool은 재시도 전에 terminate돼야 한다"
    assert not made_pools[1].terminated, "성공한 Pool은 그대로 반환되고 건드리지 않는다"


@pytest.mark.asyncio
async def test_create_pool_with_retry_retries_oserror_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same absorption applies to the raw `OSError` shape (WinError 64
    surfaces as `ConnectionResetError`, an `OSError` subclass, before asyncpg
    wraps it)."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _FakePool(
                ConnectionResetError(22, "network name no longer available", None, 64, None)
            )
        return _FakePool(None)

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert isinstance(result, _FakePool) and not result.terminated
    assert calls == 2


@pytest.mark.asyncio
async def test_create_pool_with_retry_propagates_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: a persistent reset (not just a one-off transient) still
    raises after `_POOL_CONNECT_ATTEMPTS` -- this never becomes a false green."""
    db_module = sys.modules["tests.support.db"]
    calls = 0
    made_pools: list[_FakePool] = []

    def _always_fails(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal calls
        calls += 1
        pool = _FakePool(
            asyncpg.exceptions.ConnectionDoesNotExistError("connection does not exist")
        )
        made_pools.append(pool)
        return pool

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _always_fails)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert calls == db_module._POOL_CONNECT_ATTEMPTS
    assert all(pool.terminated for pool in made_pools), "실패한 시도는 전부 terminate돼야 한다"


@pytest.mark.asyncio
async def test_create_pool_with_retry_does_not_retry_unrelated_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-transient failure (e.g. bad credentials) raises immediately on
    the first attempt -- only the documented transient-reset shape retries."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise asyncpg.exceptions.InvalidPasswordError("password authentication failed")

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)

    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert calls == 1


# ── Jitter tests: task-6687/esc-ci-coverage decorrelation ────────────


@pytest.mark.asyncio
async def test_sleep_before_pool_retry_jitters_within_retry_delay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-6687: `_sleep_before_pool_retry` must sleep
    `random.uniform(0, _pool_retry_delay(attempt))`, not the deterministic
    `_pool_retry_delay(attempt)` itself -- concurrent worktrees sharing one local
    Postgres and computing the same deterministic schedule retry in lockstep and
    repeatedly re-create the contention burst they are backing off from (see
    scripts/replay_verify.py's `_sleep_before_retry`, task-6627, same shape)."""
    db_module = sys.modules["tests.support.db"]
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr(db_module.asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(db_module.random, "uniform", lambda lo, hi: lo + (hi - lo) * 0.25)

    await _sleep_before_pool_retry(3)

    assert captured == [_pool_retry_delay(3) * 0.25]


@pytest.mark.asyncio
async def test_sleep_before_pool_retry_never_exceeds_retry_delay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative test: across many draws, the jittered sleep must never exceed (or
    go below zero of) the deterministic `_pool_retry_delay(attempt)` it is
    jittering under -- a broken jitter sampling outside
    `[0, _pool_retry_delay(attempt)]` would silently widen the retry budget past
    `_POOL_CONNECT_RETRY_BASE_DELAY`, exactly what DECISION_GUIDELINES B-2 forbids."""
    db_module = sys.modules["tests.support.db"]
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr(db_module.asyncio, "sleep", _capture_sleep)

    cap = _pool_retry_delay(4)
    for _ in range(200):
        await _sleep_before_pool_retry(4)

    assert all(0.0 <= delay <= cap for delay in captured)


@pytest.mark.asyncio
async def test_create_pool_with_retry_jitters_between_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_pool_with_retry` sleeps via the jittered helper, not a raw
    `asyncio.sleep(_pool_retry_delay(...))` call -- a regression back to the
    deterministic sleep would reintroduce the lockstep thundering herd this
    fix decorrelates."""
    db_module = sys.modules["tests.support.db"]
    calls = 0
    sleeps: list[int] = []

    def _fake_create_pool(dsn: str, **kwargs: object) -> _FakePool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _FakePool(
                asyncpg.exceptions.ConnectionDoesNotExistError(
                    "connection was closed in the middle of operation"
                )
            )
        return _FakePool(None)

    async def _fake_sleep_before_pool_retry(attempt: int) -> None:
        sleeps.append(attempt)

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_sleep_before_pool_retry", _fake_sleep_before_pool_retry)

    await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert sleeps == [0]


# ── _admin_connect_with_retry: esc-ci-pytest_latency_serial ──────────
#
# `ensure_worker_database`'s admin connect (`asyncpg.connect`, no pool
# involved) had no retry at all, unlike `create_pool_with_retry` above -- the
# exact same transient Windows TCP reset it retries for can hit this plain
# connect instead, and because `ensure_worker_database` runs at conftest.py
# *import* time (module-level `asyncio.run`), an unretried failure here
# surfaces as "ImportError while loading conftest" for the whole pytest
# invocation rather than one flaky test.


@pytest.mark.asyncio
async def test_admin_connect_with_retry_retries_transient_reset_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _fake_connect(dsn: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return "connected"

    monkeypatch.setattr(db_module.asyncpg, "connect", _fake_connect)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await _admin_connect_with_retry("postgresql://u:p@localhost/postgres")

    assert result == "connected"
    assert calls == 2


@pytest.mark.asyncio
async def test_admin_connect_with_retry_retries_oserror_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same absorption for the raw `OSError` shape (WinError 64 surfaces as
    `ConnectionResetError` before asyncpg wraps it)."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _fake_connect(dsn: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(22, "network name no longer available", None, 64, None)
        return "connected"

    monkeypatch.setattr(db_module.asyncpg, "connect", _fake_connect)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await _admin_connect_with_retry("postgresql://u:p@localhost/postgres")

    assert result == "connected"
    assert calls == 2


@pytest.mark.asyncio
async def test_admin_connect_with_retry_propagates_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: a persistent reset (not a one-off transient) still raises
    after `_POOL_CONNECT_ATTEMPTS` -- this never becomes a false green."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _always_fails(dsn: str) -> str:
        nonlocal calls
        calls += 1
        raise asyncpg.exceptions.ConnectionDoesNotExistError("connection does not exist")

    monkeypatch.setattr(db_module.asyncpg, "connect", _always_fails)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await _admin_connect_with_retry("postgresql://u:p@localhost/postgres")

    assert calls == db_module._POOL_CONNECT_ATTEMPTS


@pytest.mark.asyncio
async def test_admin_connect_with_retry_does_not_retry_unrelated_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-transient failure (e.g. bad credentials) raises immediately on
    the first attempt -- only the documented transient-reset shape retries."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _fake_connect(dsn: str) -> str:
        nonlocal calls
        calls += 1
        raise asyncpg.exceptions.InvalidPasswordError("password authentication failed")

    monkeypatch.setattr(db_module.asyncpg, "connect", _fake_connect)

    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        await _admin_connect_with_retry("postgresql://u:p@localhost/postgres")

    assert calls == 1
