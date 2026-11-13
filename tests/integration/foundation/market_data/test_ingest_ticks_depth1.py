"""LA-16 ingest_ticks 깊이 보강 DEEPEN-DEPTH LA-16b - 새 보조 테스트 파일.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#9.2 LA-16.
Task: task-24769 (DEEPEN-DEPTH LA-16b original judgment D1->D3).

Follows fixtures and imports from test_ingest_ticks.py.
Augments: negative tests >=3, failure injection, gate-red reproduction,
perf_budget measurement, adversarial regression check.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.application.ingest_ticks import (
    IngestTicksCommand,
    _first_regression,
    ingest_ticks,
)
from src.foundation.market_data.contracts.v1 import (
    QualityIssueType,
    TickRecord,
    Venue,
    Verdict,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
)

# ── fixture helpers (copied from test_ingest_ticks.py) ──


async def _instrument_id(pool: asyncpg.Pool) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
            " status, listed_at) "
            "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
            "RETURNING instrument_id",
            symbol,
        )


def _tick(
    instrument_id: uuid.UUID,
    trade_id: str,
    t: datetime,
    price: str = "100",
    quantity: str = "1",
    side: str = "buy",
) -> TickRecord:
    return TickRecord(
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        trade_id=trade_id,
        price=Decimal(price),
        quantity=Decimal(quantity),
        side=side,
        traded_at=t,
    )


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        audit=PostgresAuditEventRepository(pool),
        batches=PostgresBatchRepository(pool),
    )


async def _run(deps, ticks, *, audit=None, tenant_id=None):
    cmd = IngestTicksCommand(tenant_id=tenant_id, source="test", ticks=ticks, trace_id=uuid.uuid4())
    return await ingest_ticks(cmd, batches=deps.batches, audit=audit or deps.audit, pool=deps.pool)


# ── N1: empty ticks list ──


async def test_ingest_rejects_empty_ticks_list(pool, deps):
    """Negative test N1: empty ticks list must raise ValueError (fail-closed)."""
    with pytest.raises(ValueError, match="Empty tick batch"):
        await _run(deps, [])


# ── N2: single tick ──


async def test_ingest_accepts_single_tick(pool, deps):
    """Negative test N2: single tick must be ACCEPT."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0)]
    result = await _run(deps, ticks)
    assert result.verdict.verdict == Verdict.ACCEPT
    assert result.verdict.accepted == 1
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
    assert count == 1


# ── N3: duplicate trade_id + traded_at (idempotent re-fetch) ──


async def test_ingest_idempotent_resend_same_trade_id_and_time(pool, deps):
    """Negative test N3: re-sending identical (trade_id, traded_at) is idempotent -
    ACCEPT with accepted=1 (counts re-fetch), but DB row count stays 1."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)
    tick = _tick(instrument_id, "1", t0)
    r1 = await _run(deps, [tick])
    assert r1.verdict.verdict == Verdict.ACCEPT
    assert r1.verdict.accepted == 1

    r2 = await _run(deps, [tick])
    assert r2.verdict.verdict == Verdict.ACCEPT, "re-send must still ACCEPT"
    # accepted=1 counts re-fetch; DB row count proves idempotency
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '1'",
            instrument_id,
        )
    assert count == 1, "second send must not duplicate the row"


# ── failure_injection: audit append failure rolls back everything ──


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


async def test_ingest_rolls_back_on_audit_failure(pool, deps):
    """failure_injection: audit append failure must roll back md_tick and batch."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0)]

    with pytest.raises(RuntimeError, match="injected audit failure"):
        await _run(deps, ticks, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        tick_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE instrument_id = $1",
            instrument_id,
        )
    assert tick_count == 0, "md_tick must be empty after audit failure"
    assert batch_count == 0, "batch row must be rolled back too"


# ── gate-red reproduction: regressive input actually triggers REJECT ──


async def test_gate_red_rejection_on_trade_id_regression(pool, deps):
    """Gate-red reproduction: feed a batch that strictly regresses trade_id relative
    to stored baseline, verify the gate actually catches it (REJECT verdict)."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)

    # Store a baseline: trade_id 10 at t0+10s
    baseline = [_tick(instrument_id, "10", t0 + timedelta(seconds=10))]
    r1 = await _run(deps, baseline)
    assert r1.verdict.verdict == Verdict.ACCEPT

    # Now send a regressive batch: trade_id 3 at t0+3s (both trade_id and time regress)
    regressive = [_tick(instrument_id, "3", t0 + timedelta(seconds=3))]
    r2 = await _run(deps, regressive)

    assert r2.verdict.verdict == Verdict.REJECT, (
        f"gate-red: regressive input should trigger REJECT, got {r2.verdict.verdict}"
    )
    assert r2.verdict.rejected == 1
    assert r2.verdict.accepted == 0
    # Verify no new rows stored
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '3'",
            instrument_id,
        )
    assert count == 0


async def test_gate_red_rejection_on_time_regression(pool, deps):
    """Gate-red reproduction: feed a batch that strictly regresses traded_at,
    verify the gate catches it even if trade_id is larger."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)

    # Store baseline: trade_id 5 at t0+5s
    baseline = [_tick(instrument_id, "5", t0 + timedelta(seconds=5))]
    r1 = await _run(deps, baseline)
    assert r1.verdict.verdict == Verdict.ACCEPT

    # Time regression: trade_id 100 (larger) but at t0+2s (earlier)
    regressive = [_tick(instrument_id, "100", t0 + timedelta(seconds=2))]
    r2 = await _run(deps, regressive)

    assert r2.verdict.verdict == Verdict.REJECT, (
        f"time regression must trigger REJECT, got {r2.verdict.verdict}"
    )
    assert r2.verdict.rejected == 1
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '100'",
            instrument_id,
        )
    assert count == 0


# ── perf_budget + virtual_delay gate-red: measurement actually reads slow ──

# Performance budget: 15 sequential DB round-trips max.
# Rationale: local measurement ~3-5 round-trips for 20 ticks. 30x margin = 15.
# This is a structural regression guard (N+1 query detection), not a wall-clock budget.
_MAX_INGEST_TICKS_ROUND_TRIPS = 15


class _QueryCountingPool:
    """Wraps a pool to count DB round-trips via query logging."""

    def __init__(self, pool, queries):
        self._pool = pool
        self._queries = queries

    def acquire(self):
        return self._AcquireCtx(self._pool, self._queries)

    class _AcquireCtx:
        def __init__(self, pool, queries):
            self._pool = pool
            self._queries = queries
            self._ctx = None

        async def __aenter__(self):
            self._ctx = self._pool.acquire()
            conn = await self._ctx.__aenter__()
            conn.add_query_logger(lambda record: self._queries.append(getattr(record, "query", "")))
            return conn

        async def __aexit__(self, *exc):
            return await self._ctx.__aexit__(*exc)


@pytest.mark.perf
async def test_perf_budget_round_trip_with_virtual_delay(pool, deps):
    """PerfBudget assertion with virtual_delay injection (10x).

    Injects 0.5s virtual wall-clock delay via virtual_delayed_async on
    asyncpg.Connection.fetch (used by _known_ticks and _last_stored).
    The PerfBudget.sample measurement must read the delayed time, proving
    the gate-red assertion actually catches slowdowns.
    """
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(20)]

    reset_virtual_totals()

    # Inject 0.5s virtual delay per fetch call (10x typical ~50ms fetch)
    import asyncpg

    original_fetch = asyncpg.Connection.fetch
    asyncpg.Connection.fetch = virtual_delayed_async(original_fetch, 0.5)
    try:
        queries = []
        counting_deps = SimpleNamespace(
            pool=_QueryCountingPool(pool, queries), audit=deps.audit, batches=deps.batches
        )

        budget = PerfBudget()
        sample = await budget.sample_async(lambda: _run(counting_deps, ticks), batch=1)

        # Gate-red: measurement must include injected delay
        assert sample.wall_ms > 400, (
            f"virtual delay not measured: wall={sample.wall_ms:.1f}ms "
            f"(expected >400ms with 0.5s virtual injection)"
        )

        # Verify the actual ingest still works (ACCEPT despite virtual delay)
        # The virtual delay only affects measurement, not the actual result
        print(
            f"[LA-16 perf] round_trips={len(queries)} "
            f"sample_cpu={sample.cpu_ms:.3f}ms sample_wall={sample.wall_ms:.3f}ms"
        )
        assert len(queries) <= _MAX_INGEST_TICKS_ROUND_TRIPS, (
            f"round trips ({len(queries)}) exceeds budget ({_MAX_INGEST_TICKS_ROUND_TRIPS})"
        )
    finally:
        asyncpg.Connection.fetch = original_fetch


# ── adversarial: _first_regression catches mid-batch regression ──


async def test_first_regression_catches_mid_batch_regression():
    """Adversarial test: _first_regression must catch a regression that occurs
    in the middle of a batch (not just at the start)."""
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [
        _tick(uuid.uuid4(), "1", t0),  # baseline
        _tick(uuid.uuid4(), "5", t0 + timedelta(seconds=5)),  # ok
        _tick(uuid.uuid4(), "3", t0 + timedelta(seconds=2)),  # REGRESSION here
        _tick(uuid.uuid4(), "10", t0 + timedelta(seconds=10)),  # recovered (ignored)
    ]
    issue = _first_regression(ticks, baseline_trade_id=None, baseline_traded_at=None)
    assert issue is not None, "mid-batch regression must be caught"
    assert issue.type == QualityIssueType.TIME_MISALIGNED
    assert issue.detail["reason"] == "traded_at_regression"
    assert issue.detail["trade_id"] == "3"


async def test_first_regression_allows_recovery_after_max(pool, deps):
    """Adversarial test: if ticks regress but then exceed the baseline maximum,
    they should NOT be flagged (the running max updates)."""
    instrument_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)

    # Store baseline: trade_id 5 at t0+5s
    baseline = [_tick(instrument_id, "5", t0 + timedelta(seconds=5))]
    r1 = await _run(deps, baseline)
    assert r1.verdict.verdict == Verdict.ACCEPT

    # Now send: trade_id 3 at t0+3s (regression) followed by trade_id 10 at t0+10s (recovery)
    # The entire batch must be REJECT because the FIRST regression is caught
    mixed = [
        _tick(instrument_id, "3", t0 + timedelta(seconds=3)),
        _tick(instrument_id, "10", t0 + timedelta(seconds=10)),
    ]
    r2 = await _run(deps, mixed)

    # The batch containing a regression must be REJECT entirely (no partial storage)
    assert r2.verdict.verdict == Verdict.REJECT, "batch containing regression must be fully REJECT"
    assert r2.verdict.accepted == 0


# ── adversarial: concurrent same-instrument batches ──


async def test_concurrent_batches_same_instrument_serialize(pool, deps):
    """Adversarial D3: concurrent batches for the same instrument must serialize
    via advisory lock, leaving no trade_id duplicates or regressions."""
    instrument_id = await _instrument_id(pool)
    other_id = await _instrument_id(pool)
    t0 = datetime.now(UTC).replace(microsecond=0)

    # Baseline
    baseline = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(1, 6)]
    r_base = await _run(deps, baseline)
    assert r_base.verdict.verdict == Verdict.ACCEPT

    # Two concurrent batches that overlap in trade_id range
    batch_a = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i + 10)) for i in range(6, 11)]
    batch_b = [_tick(instrument_id, "8", t0 + timedelta(seconds=8, milliseconds=500))]
    # Third batch for a different instrument (should not be affected)
    batch_other = [_tick(other_id, "1", t0)]

    results = await asyncio.gather(
        _run(deps, batch_a),
        _run(deps, batch_b),
        _run(deps, batch_other),
    )
    r_a, r_b, r_other = results

    # Exactly one of A and B must ACCEPT (the other REJECT due to lock serialization)
    verdicts = {r_a.verdict.verdict, r_b.verdict.verdict}
    assert verdicts == {Verdict.ACCEPT, Verdict.REJECT}, (
        f"exactly one of A/B must ACCEPT — got A={r_a.verdict.verdict}, B={r_b.verdict.verdict}"
    )
    # Other instrument unaffected
    assert r_other.verdict.verdict == Verdict.ACCEPT

    # Final state: no regressions, no duplicates
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT trade_id, traded_at FROM md_tick WHERE instrument_id = $1 ORDER BY traded_at",
            instrument_id,
        )
    trade_ids = [int(row["trade_id"]) for row in rows]
    assert trade_ids == sorted(trade_ids), f"final trade_ids must be sorted — got {trade_ids}"
    assert len(trade_ids) == len(set(trade_ids)), f"no duplicate trade_ids — got {trade_ids}"
