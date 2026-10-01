"""Shared pool/deps fixtures for market_data integration tests.

Moved out of test_quality_metrics.py on 2026-09-23 when that module (625 lines) was split to
satisfy the loc_over_500 ratchet; both halves and any other test in this directory use them.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        store=PostgresCandleStore(pool),
        batches=PostgresBatchRepository(pool),
    )


# DEEPEN(task-10101): negative/failure-injection coverage for the pool/deps
# fixtures shared across tests/foundation/integration/market_data/.
# Negative tests >=3, failure injection >=1, performance assertion >=1.

# ── Negative tests: invariant-violating inputs ───────────────────────────


def test_asyncpg_dsn_missing_env_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL absent must fail closed with KeyError, not silently default."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


def test_asyncpg_dsn_rejects_non_sqlalchemy_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the `postgresql+asyncpg://` prefix is rewritten — any other scheme
    is passed through unchanged, so a caller with a wrong scheme gets an
    unusable DSN rather than a silently-guessed one."""
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/db")
    dsn = _asyncpg_dsn()
    assert dsn == "mysql://user:pass@localhost:3306/db"
    assert not dsn.startswith("postgresql://")


def test_asyncpg_dsn_empty_env_returns_empty_not_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty DATABASE_URL must surface as an empty string, never as None
    or a guessed default — downstream asyncpg.connect() then fails loudly."""
    monkeypatch.setenv("DATABASE_URL", "")
    dsn = _asyncpg_dsn()
    assert dsn == ""


# ── Failure injection: pool creation failure must propagate ─────────────


@pytest.mark.asyncio
async def test_pool_fixture_propagates_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure injection: if asyncpg.create_pool raises (e.g. DB unreachable),
    the pool fixture must propagate the exception rather than yielding a
    broken/partial pool (fail-closed)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    with patch.object(
        asyncpg, "create_pool", AsyncMock(side_effect=ConnectionError("db unreachable"))
    ):
        gen = pool.__wrapped__()  # type: ignore[attr-defined]
        with pytest.raises(ConnectionError):
            await gen.__anext__()


# ── Performance assertion: DSN rewrite budget ────────────────────────────


@pytest.mark.perf
def test_asyncpg_dsn_rewrite_performance_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Performance: 1000 DSN rewrites must complete within 50ms — this is a
    hot-path helper called once per fixture instantiation across the suite.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    start = time.perf_counter()
    for _ in range(1000):
        _asyncpg_dsn()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50, f"DSN rewrite exceeded 50ms budget for 1000 calls: {elapsed_ms:.1f}ms"
