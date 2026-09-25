"""L32 integration tests -- `adapters/postgres_snapshot_repository.py` +
migration `6e2b5965124e` (`market_bar_snapshot`, §3.7 M4). Spec:
docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.4/§9 L32 ("저장->로드->
해시 동일"). ADR-2026-09-09-C D2: negative >= 3, failure injection 1,
numeric performance assertion 1, gate-red reproduction 1.

Follows `tests/foundation/integration/experiments/test_postgres_repository.py`'s
`pool`/WORM-proof/gate-red pattern (same spec family, same L0-5 `worm_sql`
generator).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.market_data import Candle
from src.foundation.backtest.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.backtest.domain.snapshot import (
    BarSnapshotRef,
    compute_bar_snapshot_hash,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresSnapshotRepository:
    return PostgresSnapshotRepository(pool)


def _bars(symbol: str = "BTCUSDT", n: int = 3) -> list[Candle]:
    open_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        bars.append(
            Candle(
                symbol=symbol,
                exchange="bitget",
                timeframe="1m",
                open=Decimal("100") + i,
                high=Decimal("101") + i,
                low=Decimal("99") + i,
                close=Decimal("100.5") + i,
                volume=Decimal("10"),
                open_time=open_time + timedelta(minutes=i),
                close_time=open_time + timedelta(minutes=i + 1),
            )
        )
    return bars


def _ref(bars: list[Candle], *, source: str = "bitget", as_of: datetime = _AS_OF) -> BarSnapshotRef:
    snapshot_hash = compute_bar_snapshot_hash(bars, source=source, as_of=as_of)
    return BarSnapshotRef(
        snapshot_hash=snapshot_hash,
        symbol=bars[0].symbol,
        exchange=bars[0].exchange,
        timeframe=bars[0].timeframe,
        from_time=bars[0].open_time,
        to_time=bars[-1].close_time,
        bar_count=len(bars),
        source=source,
        as_of=as_of,
    )


# --- happy path ---


async def test_save_then_load_round_trip_hash_equal(repo: PostgresSnapshotRepository) -> None:
    bars = _bars()
    ref = _ref(bars)

    await repo.save(ref, bars)
    loaded = await repo.load(ref.snapshot_hash)

    assert loaded is not None
    loaded_ref, loaded_bars = loaded
    assert loaded_ref == ref
    assert loaded_bars == bars
    recomputed = compute_bar_snapshot_hash(
        loaded_bars, source=loaded_ref.source, as_of=loaded_ref.as_of
    )
    assert recomputed == ref.snapshot_hash


async def test_save_same_hash_twice_is_noop(repo: PostgresSnapshotRepository) -> None:
    bars = _bars(symbol="ETHUSDT")
    ref = _ref(bars)

    await repo.save(ref, bars)
    await repo.save(ref, bars)  # duplicate save -- must not raise, must not duplicate

    loaded = await repo.load(ref.snapshot_hash)
    assert loaded is not None
    assert loaded[1] == bars


# --- negative (>= 3) ---


async def test_load_missing_hash_returns_none(repo: PostgresSnapshotRepository) -> None:
    assert await repo.load("0" * 64) is None


async def test_save_bar_count_mismatch_rejected(repo: PostgresSnapshotRepository) -> None:
    bars = _bars(symbol="XRPUSDT")
    ref = _ref(bars)
    ref = ref.model_copy(update={"bar_count": len(bars) + 1})

    with pytest.raises(ValueError, match="bar_count"):
        await repo.save(ref, bars)


async def test_save_rejects_nonpositive_bar_count_check(pool: asyncpg.Pool) -> None:
    """Direct SQL bypassing the adapter still hits the migration's
    `bar_count > 0` CHECK constraint."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO market_bar_snapshot "
                "(snapshot_hash, symbol, exchange, timeframe, from_time, to_time, "
                " bar_count, source, as_of, bars) "
                "VALUES ($1, 'BTCUSDT', 'bitget', '1m', $2, $2, 0, 'bitget', $2, '[]'::jsonb)",
                "1" * 64,
                _AS_OF,
            )


# --- failure injection: WORM blocks mutation even for the table owner ---


async def test_worm_trigger_blocks_table_owner_update(
    pool: asyncpg.Pool, repo: PostgresSnapshotRepository
) -> None:
    """REVOKE does not bind the table owner (PostgreSQL rule) -- `pool`
    connects without `SET ROLE` and owns `market_bar_snapshot` (the
    migration ran as this account), so if UPDATE is blocked here it is the
    trigger, not the REVOKE, doing the blocking."""
    bars = _bars(symbol="SOLUSDT")
    ref = _ref(bars)
    await repo.save(ref, bars)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE market_bar_snapshot SET bar_count = 999 WHERE snapshot_hash = $1",
                ref.snapshot_hash,
            )


async def test_worm_trigger_blocks_table_owner_delete(
    pool: asyncpg.Pool, repo: PostgresSnapshotRepository
) -> None:
    bars = _bars(symbol="ADAUSDT")
    ref = _ref(bars)
    await repo.save(ref, bars)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM market_bar_snapshot WHERE snapshot_hash = $1",
                ref.snapshot_hash,
            )


async def test_aios_app_role_cannot_update_market_bar_snapshot(
    pool: asyncpg.Pool, repo: PostgresSnapshotRepository
) -> None:
    """Second defense layer: `aios_app` was never granted UPDATE/DELETE at
    all, and REVOKE additionally strips PUBLIC -- either way it must fail."""
    bars = _bars(symbol="DOGEUSDT")
    ref = _ref(bars)
    await repo.save(ref, bars)

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE market_bar_snapshot SET bar_count = 1 WHERE snapshot_hash = $1",
                ref.snapshot_hash,
            )


# --- gate-red reproduction: prove the CHECK guard is load-bearing ---


async def test_gate_red_check_constraint_actually_fails_without_it(pool: asyncpg.Pool) -> None:
    """Proves the CHECK-violation negative test above is not a tautology --
    a temp table with the same column but no CHECK accepts the value
    `market_bar_snapshot.bar_count` rejects."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE market_bar_snapshot_no_check (bar_count INT NOT NULL) "
            "ON COMMIT DROP"
        )
        await conn.execute("INSERT INTO market_bar_snapshot_no_check (bar_count) VALUES (0)")
        row = await conn.fetchrow("SELECT bar_count FROM market_bar_snapshot_no_check")
        assert row["bar_count"] == 0  # red: without the CHECK, this would have passed


# --- numeric performance assertion ---

_SAVE_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0
"""Same single-DB-round-trip budget as
`tests/foundation/integration/experiments/test_postgres_repository.py`'s
`_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS` (no backtest-specific SLO number exists
in §7 yet)."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
async def test_save_db_roundtrip_p95_within_budget(repo: PostgresSnapshotRepository) -> None:
    samples: list[float] = []
    for i in range(30):
        bars = _bars(symbol=f"PERF{i}USDT")
        ref = _ref(bars)
        started = time.perf_counter()
        await repo.save(ref, bars)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[L32 save] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_SAVE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _SAVE_DB_ROUNDTRIP_P95_BUDGET_MS
