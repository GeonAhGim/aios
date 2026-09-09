"""L4-29(task-2180) DEEPEN(task-2796) — failure-injection + numeric perf.

DEPTH audit (task-2722, docs/audit/DEPTH_L4_BR.md #2180) graded the original
leaf (commit 03efa422) D1, below the D2 floor: `test_live_mode_bypass_attempts.py` and
`test_tampered_provider_event.py` have strong adversarial/replay coverage
(3 bypass paths, 3 tamper vectors, all with negative assertions) but no
failure-injection test (simulated DB/network fault) and no numeric
performance/latency assertion anywhere in the L4-29 surface. This file adds
exactly those two things and touches no production code -- both guards
(Executor's `mode != "PAPER"` check and `require_paper_sandbox`) and both
fail-closed branches (`InboxProcessor._process_row`'s venue-mismatch check)
already behave correctly; this only proves it under simulated faults and
puts a number on the cost.

Reuses the sibling files' helpers instead of duplicating seeding/fixture
logic (`tests/adversarial/oms/conftest.py` re-export pattern).

Part A (path1/path3, `test_live_mode_bypass_attempts.py`): the LIVE-mode
gate in `Executor.execute()` is the *first* statement after the
`risk_result.approved` assert -- strictly before `_read_intent_counter`,
the first DB access. A pool that raises on `.acquire()` therefore proves
the gate is unreachable-by-DB-fault: it blocks identically whether the DB
is healthy or completely down, and the timing loop shows the rejection
costs no I/O (safe as an absolute wall-clock budget for the same reason
task-2791's in-memory perf test was safe -- no DB/network round trip is
possible through a pool object that raises the instant `.acquire()` is
called).

Part B (path2, `test_live_mode_bypass_attempts.py`): `require_paper_sandbox`
reads only `self.is_paper_trading`/`self.is_sandboxed` (in-memory
properties) before ever calling into `func`, so it must block identically
whether the network is reachable or not. Swaps the existing "must not be
called" transport assertion for a transport that raises a simulated
`httpx.ConnectError` -- if the guard ever regressed to check *after*
issuing the request, this test would surface a `ConnectError` instead of
`FrozenZonePaperAdapterBlockedError`.

Part C (`test_tampered_provider_event.py`): injects a simulated DB/network
fault (`ConnectionResetError`) into `OrderRepoPort.get_for_update` -- the
exact call `_process_row` makes to fetch the order before deciding
`is_terminal(order.status)` for a replay-after-terminal-fill (double-spend)
tamper attempt. Of the three tamper vectors, this is the only one that
reaches `get_for_update` at all (venue-spoofing and forged-reference both
reject earlier, inside `_resolve_order_id`'s SQL, before any order row is
fetched). Proves the fault aborts the *whole* transaction (the
`provider_event_inbox` row from `insert_if_absent` is rolled back too, not
left half-applied) and that a healthy processor can still correctly reject
the same tampered replay afterwards (retry-safe, not stuck).

Part D: numeric round-trip budget for rejecting a batch of forged-reference
tamper events, using the same `single_conn_pool` + query-logger technique
as `test_duplicate_delivery_gate.py` (absolute wall-clock is print-only,
per-call round-trip count is the CI-safe gate -- esc-826/task-1038/1521
decision, same rationale as that file's docstring).
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from src.core.exceptions import FrozenZoneLiveModeBlockedError, FrozenZonePaperAdapterBlockedError
from src.exchanges.bitget.adapter import BitgetAdapter
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_views import OrderView
from tests.adversarial.oms.test_live_mode_bypass_attempts import (
    _execute,
    _order,
)
from tests.adversarial.oms.test_live_mode_bypass_attempts import (
    _seed_execution as _seed_live_mode_execution,
)
from tests.adversarial.oms.test_tampered_provider_event import (
    _counts,
    _fill_event,
    _insert_order,
)
from tests.adversarial.oms.test_tampered_provider_event import (
    _seed_execution as _seed_tamper_execution,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

# ---------------------------------------------------------------------------
# Part A -- LIVE-mode gate under a simulated total DB/network outage.
# ---------------------------------------------------------------------------


class _PoolThatFailsOnAcquire:
    """Duck-typed `asyncpg.Pool` stand-in that raises the instant
    `.acquire()` is called -- simulating a DB/network outage. If the
    LIVE-mode gate ever moved after a DB access, tests using this pool
    would surface `ConnectionResetError` instead of
    `FrozenZoneLiveModeBlockedError`, and the perf loop below would no
    longer be a safe absolute-wall-clock assertion."""

    def acquire(self) -> Any:
        raise ConnectionResetError(
            "simulated DB/network outage -- Executor.execute() must reject a "
            "LIVE-mode submission before ever touching the pool"
        )


async def test_path1_live_mode_block_survives_total_db_outage(pool: asyncpg.Pool) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_live_mode_execution(pool, user_id, mode="LIVE")
    adapter = FakeExchangeAdapter()

    with pytest.raises(FrozenZoneLiveModeBlockedError):
        await _execute(
            _PoolThatFailsOnAcquire(), execution_id, user_id, mode="LIVE", adapter=adapter
        )

    assert adapter.place_order_call_count == 0


async def test_path3_spoofed_adapter_live_mode_block_survives_total_db_outage(
    pool: asyncpg.Pool,
) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_live_mode_execution(pool, user_id, mode="LIVE")
    spoofed_adapter = FakeExchangeAdapter(is_paper_trading=True, is_sandboxed=True)

    with pytest.raises(FrozenZoneLiveModeBlockedError):
        await _execute(
            _PoolThatFailsOnAcquire(),
            execution_id,
            user_id,
            mode="LIVE",
            adapter=spoofed_adapter,
        )

    assert spoofed_adapter.place_order_call_count == 0


@pytest.mark.perf
async def test_live_mode_rejection_is_o1_with_no_db_round_trip(pool: asyncpg.Pool) -> None:
    """Numeric perf: rejecting 500 LIVE-mode submissions through a pool
    that would raise on any `.acquire()` call completes well under a
    generous per-call budget -- safe as an absolute wall-clock assertion
    because no DB/network I/O can occur at all on this path (proven by
    Part A above); any regression that let the gate fall through to a real
    I/O attempt would instead raise `ConnectionResetError` from the fake
    pool, failing this test outright rather than just slowing it down."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_live_mode_execution(pool, user_id, mode="LIVE")
    adapter = FakeExchangeAdapter()
    broken_pool = _PoolThatFailsOnAcquire()
    n = 500

    started = time.perf_counter()
    for _ in range(n):
        with pytest.raises(FrozenZoneLiveModeBlockedError):
            await _execute(broken_pool, execution_id, user_id, mode="LIVE", adapter=adapter)
    elapsed_sec = time.perf_counter() - started

    per_call_ms = (elapsed_sec / n) * 1000
    print(
        f"\nLIVE-mode rejection: {n} attempts in {elapsed_sec:.3f}s = "
        f"{per_call_ms:.4f} ms/call (no DB/network I/O possible on this path)"
    )
    assert adapter.place_order_call_count == 0
    assert per_call_ms < 5.0, (
        f"LIVE-mode rejection averaged {per_call_ms:.4f} ms/call (budget=5.0) -- "
        "this path does zero I/O, so a regression here means real work leaked "
        "into the hot rejection path."
    )


# ---------------------------------------------------------------------------
# Part B -- require_paper_sandbox under a simulated network outage.
# ---------------------------------------------------------------------------


def _network_fault_transport() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network outage", request=request)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)


async def test_path2_guard_blocks_before_touching_a_dead_network() -> None:
    """If `require_paper_sandbox` ever regressed to check its condition
    *after* issuing the HTTP request (e.g. a refactor that inlines the
    check into `func` instead of the wrapper), this test would surface
    `httpx.ConnectError` from the dead transport instead of
    `FrozenZonePaperAdapterBlockedError` -- proving the guard fires purely
    from in-memory state, independent of network reachability."""
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=_network_fault_transport()
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


# ---------------------------------------------------------------------------
# Part C -- InboxProcessor under a simulated DB/network fault mid-tamper-check.
# ---------------------------------------------------------------------------


class _FaultInjectingOrderRepo:
    """Wraps the real `PostgresOrderRepository` but raises a simulated
    DB/network fault on `get_for_update` -- the exact call `_process_row`
    makes to fetch the order before checking `is_terminal(order.status)`
    against a replayed (tampered) fill event. `transition` and the other
    protocol methods delegate untouched since this scenario never reaches
    them (the fault fires first)."""

    def __init__(self, real: PostgresOrderRepository) -> None:
        self._real = real

    async def get_for_update(self, conn: asyncpg.Connection, order_id: UUID) -> OrderView:
        raise ConnectionResetError(
            "simulated DB/network fault while validating a tampered provider event"
        )

    async def transition(self, conn: asyncpg.Connection, **kwargs: Any) -> OrderView:
        return await self._real.transition(conn, **kwargs)  # pragma: no cover -- unreached here

    async def find_by_scope_hash(self, conn: asyncpg.Connection, scope_hash: str) -> Any:
        return await self._real.find_by_scope_hash(conn, scope_hash)  # pragma: no cover

    async def list_children_for_update(
        self, conn: asyncpg.Connection, parent_order_id: UUID
    ) -> Any:
        return await self._real.list_children_for_update(conn, parent_order_id)  # pragma: no cover

    async def set_committed_child_qty(self, conn: asyncpg.Connection, **kwargs: Any) -> Any:
        return await self._real.set_committed_child_qty(conn, **kwargs)  # pragma: no cover


async def test_replay_after_terminal_fill_db_fault_rolls_back_cleanly_and_is_retry_safe(
    pool: asyncpg.Pool,
) -> None:
    """The venue-spoofed and forged-reference vectors both reject an event
    before ever calling `get_for_update` (`_resolve_order_id`'s SQL already
    filters `exchange = ev.venue`, so a spoofed venue simply finds no row --
    same as a forged reference). The replay-after-terminal-fill vector is
    the one tamper path that *does* reach `get_for_update` -- it needs the
    real order's `status` to decide `is_terminal()` -- so it is the one
    where a DB/network fault can land exactly inside the fail-closed
    check, and the one this test targets."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_tamper_execution(pool, user_id)
    quantity = Decimal("5")
    exchange_order_id = f"ex-tamper-fault-replay-{uuid4().hex}"
    order_id, client_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=quantity,
        exchange_order_id=exchange_order_id,
    )
    legit = _fill_event(
        venue="bitget", client_order_id=client_order_id, exchange_order_id=exchange_order_id,
        quantity=quantity, provider_event_id=f"tamper-fault-legit-{uuid4().hex}",
    )
    healthy_processor = InboxProcessor(pool)
    await healthy_processor.ingest(legit)
    before = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert before["status"] == "FILLED"
    assert before["fills_count"] == 1

    tampered_replay = _fill_event(
        venue="bitget", client_order_id=client_order_id, exchange_order_id=exchange_order_id,
        quantity=quantity * 10,  # inflated (tampered) double-spend attempt
        provider_event_id=f"tamper-fault-replay-{uuid4().hex}",
    )
    faulty_processor = InboxProcessor(
        pool, order_repo=_FaultInjectingOrderRepo(PostgresOrderRepository())
    )

    with pytest.raises(ConnectionResetError):
        await faulty_processor.ingest(tampered_replay)

    # Whole transaction rolled back -- the inbox row from insert_if_absent
    # must not survive the fault either (would otherwise wedge the event
    # in a half-applied state that neither retries nor reports PROCESSED).
    async with pool.acquire() as conn:
        inbox_row_count = await conn.fetchval(
            "SELECT count(*) FROM provider_event_inbox WHERE venue = 'bitget' "
            "AND provider_event_id = $1",
            tampered_replay.provider_event_id,
        )
    assert inbox_row_count == 0

    result = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert result["status"] == "FILLED"
    assert result["filled_quantity"] == quantity  # inflated replay not applied
    assert result["fills_count"] == 1  # unchanged
    assert result["positions_count"] == 1  # unchanged

    # Retry-safe: a healthy processor (no injected fault) can still ingest
    # the exact same tampered replay afterwards and correctly reject it --
    # the fault above did not corrupt any state that would let the double
    # spend through or get the event permanently stuck.
    resumed = await healthy_processor.ingest(tampered_replay)
    assert resumed is True
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE venue = 'bitget' "
            "AND provider_event_id = $1",
            tampered_replay.provider_event_id,
        )
    assert state == "PROCESSED"  # terminal-replay semantics, same as the sibling test
    result_after_retry = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert result_after_retry["fills_count"] == 1
    assert result_after_retry["filled_quantity"] == quantity


# ---------------------------------------------------------------------------
# Part D -- numeric round-trip budget for rejecting forged-reference events.
# ---------------------------------------------------------------------------

_FORGED_REJECTION_N = 100
# BEGIN + INSERT(ON CONFLICT) + SELECT id + resolve SELECT(order lookup,
# no match) + mark_ignored UPDATE + COMMIT + RESET, measured.
_FORGED_ROUND_TRIPS_PER_CALL = 7


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def single_conn_pool() -> asyncpg.Pool:
    """Counting exact round trips requires every call to share one
    physical connection (same reasoning as
    `test_duplicate_delivery_gate.py::single_conn_pool`)."""
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=1)
    yield p
    await p.close()


async def _attach_round_trip_logger(pool: asyncpg.Pool) -> list[str]:
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    conn = await pool.acquire()
    conn.add_query_logger(_log)
    await pool.release(conn)
    return queries


@pytest.mark.perf
async def test_forged_reference_rejection_throughput_and_round_trip_budget(
    single_conn_pool: asyncpg.Pool,
) -> None:
    pool = single_conn_pool
    user_id = await create_test_tenant(pool)
    await _seed_tamper_execution(pool, user_id)
    processor = InboxProcessor(pool)

    warmup = _fill_event(
        venue="bitget", client_order_id=f"cid-forged-{uuid4().hex}",
        exchange_order_id=f"ex-forged-{uuid4().hex}", quantity=Decimal("1"),
        provider_event_id=f"perf-forged-warmup-{uuid4().hex}",
    )
    assert await processor.ingest(warmup) is True  # warm-up, outside the budget

    queries = await _attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    for _ in range(_FORGED_REJECTION_N):
        ev = _fill_event(
            venue="bitget", client_order_id=f"cid-forged-{uuid4().hex}",
            exchange_order_id=f"ex-forged-{uuid4().hex}", quantity=Decimal("999"),
            provider_event_id=f"perf-forged-{uuid4().hex}",
        )
        assert await processor.ingest(ev) is True  # 삽입은 성공, 매칭 실패로 IGNORED
    elapsed_sec = time.perf_counter() - started

    achieved_per_sec = _FORGED_REJECTION_N / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = _FORGED_REJECTION_N * _FORGED_ROUND_TRIPS_PER_CALL
    print(
        f"\nforged-reference rejection: {_FORGED_REJECTION_N} events in "
        f"{elapsed_sec:.3f}s = {achieved_per_sec:.1f} rejected/s (non-gating); "
        f"round trips={len(queries)} (budget={expected_round_trips})"
    )
    assert len(queries) == expected_round_trips, (
        f"forged-reference rejection round trips ({len(queries)}) diverged from the "
        f"measured budget ({expected_round_trips}) -- _process_row's no-match branch "
        "no longer does the same fixed amount of work per event."
    )
