"""FA-15 -- `scripts/replay_verify.py` initial-connect retry.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15.

esc-ci-replay_verify.json: local Windows CI hit an intermittent reset on the
very first TCP handshake to Postgres (WinError 64 -> asyncpg
ConnectionDoesNotExistError) inside `asyncpg.create_pool`, before any query
ran -- a transient OS-level flake, not a code regression (bisect landed on an
unrelated comment-only commit). `_create_pool_with_retry` retries that one
connect a bounded number of times; these tests never touch a real socket --
`asyncpg.create_pool` itself is monkeypatched -- so they run without
TEST_DATABASE_URL.
"""

from __future__ import annotations

import time

import asyncpg
import pytest

from scripts import replay_verify

pytestmark = pytest.mark.asyncio


class _FakePool:
    pass


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


async def _no_sleep(delay: float) -> None:
    return None
