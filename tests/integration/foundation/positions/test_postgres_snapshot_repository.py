"""PostgresSnapshotRepository integration tests -- real DB (TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-9.
DoD(task-375): a stale conditional upsert must never overwrite the latest
snapshot (negative -- stale `expected_seq` is rejected, latest value kept).

FA-0d-fix (task-771991202, depth D3 safety axis): `upsert` now persists the
key's `portfolio_id` into the `pos_snapshot.portfolio_id` column (root cause
of CI red 77871f67 -- the column stayed NULL and the FA-0d backfill migration
failed closed on every round trip) and fences the write on portfolio
ownership. Keys are therefore always 5-part `PositionKey`s pointing at the
tenant's bootstrapped default portfolio.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency, Money
from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, Lot, PositionSnapshotView
from src.foundation.positions.domain.position_key import InvalidPositionKeyError, PositionKey
from src.foundation.positions.ports.snapshot_repository import (
    SnapshotPortfolioError,
    SnapshotPortfolioNotFoundError,
    SnapshotPortfolioTenantMismatchError,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account


@pytest.fixture
def repo(pool):
    return PostgresSnapshotRepository(pool)


def _key(tenant_id: UUID, *, portfolio_id: UUID | None = None) -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id) if portfolio_id is None else portfolio_id,
        )
    )


def _snapshot(*, tenant_id, account_id, position_key, quantity, last_journal_seq, **overrides):
    base = dict(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid.uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("100"), currency=Currency.KRW),
        cost_method=CostMethod.FIFO,
        lots=[
            Lot(quantity=quantity, unit_cost=Decimal("100"), opened_at=datetime.now(timezone.utc))
        ]
        if quantity
        else [],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.KRW,
        last_journal_seq=last_journal_seq,
        updated_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return PositionSnapshotView(**base)


async def _setup(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return tenant_id, account_id


async def _row(pool, position_key: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT tenant_id, quantity, last_journal_seq, fund_id, portfolio_id "
            "FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )


async def _count(pool, position_key: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE position_key = $1", position_key
        )


async def test_get_returns_none_when_absent(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, uuid.uuid4(), f"missing:{uuid.uuid4().hex}") is None


async def test_get_returns_none_for_wrong_tenant(pool, repo):
    """task-489/LB-18: even if `position_key` exists, a different owner
    `tenant_id` gets `None` -- indistinguishable from absent (no existence leak)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("0"),
        last_journal_seq=0,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, snapshot, expected_seq=0)

    attacker_id = uuid.uuid4()
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, attacker_id, position_key) is None


async def test_upsert_creates_row_on_first_call_with_expected_seq_zero(pool, repo):
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("0"),
        last_journal_seq=0,
    )

    async with pool.acquire() as conn, conn.transaction():
        created = await repo.upsert(conn, snapshot, expected_seq=0)

    assert created.position_key == position_key
    assert created.last_journal_seq == 0
    assert created.quantity == Decimal("0")

    async with pool.acquire() as conn, conn.transaction():
        fetched = await repo.get(conn, tenant_id, position_key)
    assert fetched is not None
    assert fetched.last_journal_seq == 0


async def test_upsert_writes_portfolio_id_and_fund_id_columns_from_position_key(pool, repo):
    """Gate-red reproduction (task-771991202): before the fix the adapter left
    `pos_snapshot.portfolio_id` NULL (the id lived only inside the key string),
    which made the FA-0d backfill migration (`cdb114b6903f`) fail closed on
    every downgrade/upgrade round trip. Both FA-4 columns must be populated,
    and must survive the optimistic-lock replace path."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                quantity=Decimal("0"), last_journal_seq=0,
            ),
            expected_seq=0,
        )
    created = await _row(pool, position_key)
    assert created is not None
    assert created["portfolio_id"] == default_portfolio_id(tenant_id)
    assert created["fund_id"] == default_fund_id(tenant_id)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                quantity=Decimal("2"), last_journal_seq=1,
            ),
            expected_seq=0,
        )
    replaced = await _row(pool, position_key)
    assert replaced is not None
    assert replaced["last_journal_seq"] == 1
    assert replaced["portfolio_id"] == default_portfolio_id(tenant_id)
    assert replaced["fund_id"] == default_fund_id(tenant_id)


async def test_upsert_rejects_legacy_or_malformed_position_key(pool, repo):
    """negative -- a 4-part legacy key or an opaque string cannot be written:
    it would be unre-keyable by FA-0d, so it is refused up front (no row)."""
    tenant_id, account_id = await _setup(pool)
    for bad_key in (
        f"TESTVENUE:INST{uuid.uuid4().hex[:8]}:default:paper",
        f"pos:{uuid.uuid4().hex}",
        f"TESTVENUE:INST{uuid.uuid4().hex[:8]}:default:paper:not-a-uuid",
    ):
        with pytest.raises(InvalidPositionKeyError):
            async with pool.acquire() as conn, conn.transaction():
                await repo.upsert(
                    conn,
                    _snapshot(
                        tenant_id=tenant_id, account_id=account_id, position_key=bad_key,
                        quantity=Decimal("0"), last_journal_seq=0,
                    ),
                    expected_seq=0,
                )
        assert await _count(pool, bad_key) == 0


async def test_upsert_rejects_portfolio_that_was_never_bootstrapped(pool, repo):
    """negative -- missing portfolio => clear port-level error, not an FK
    violation from the driver, and no row is written."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id, portfolio_id=uuid.uuid4())
    with pytest.raises(SnapshotPortfolioNotFoundError) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                    quantity=Decimal("0"), last_journal_seq=0,
                ),
                expected_seq=0,
            )
    assert isinstance(exc_info.value, SnapshotPortfolioError)
    assert "does not exist" in str(exc_info.value)
    assert await _count(pool, position_key) == 0


async def test_upsert_rejects_cross_tenant_portfolio_and_leaves_victim_row_untouched(pool, repo):
    """negative -- an attacker tenant writing under the victim's key (whose
    portfolio the victim owns) is rejected with the tenant-mismatch error and
    the victim's current version is neither deleted nor replaced. Also covers
    the brand-new-key case: no row is created for a portfolio the tenant does
    not own."""
    victim_id, victim_account = await _setup(pool)
    attacker_id, attacker_account = await _setup(pool)
    position_key = _key(victim_id)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=victim_id, account_id=victim_account, position_key=position_key,
                quantity=Decimal("3"), last_journal_seq=1,
            ),
            expected_seq=0,
        )

    for expected_seq in (1, 0):  # matching seq (replace path) and first-creation path
        with pytest.raises(SnapshotPortfolioTenantMismatchError):
            async with pool.acquire() as conn, conn.transaction():
                await repo.upsert(
                    conn,
                    _snapshot(
                        tenant_id=attacker_id, account_id=attacker_account,
                        position_key=position_key, quantity=Decimal("999"), last_journal_seq=2,
                    ),
                    expected_seq=expected_seq,
                )
    victim_row = await _row(pool, position_key)
    assert victim_row is not None
    assert victim_row["tenant_id"] == victim_id
    assert victim_row["quantity"] == Decimal("3")
    assert victim_row["last_journal_seq"] == 1
    assert await _count(pool, position_key) == 1

    fresh_key = _key(victim_id)
    with pytest.raises(SnapshotPortfolioTenantMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=attacker_id, account_id=attacker_account, position_key=fresh_key,
                    quantity=Decimal("1"), last_journal_seq=0,
                ),
                expected_seq=0,
            )
    assert await _count(pool, fresh_key) == 0


async def test_upsert_with_matching_expected_seq_updates_row(pool, repo):
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    initial = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("0"),
        last_journal_seq=0,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, initial, expected_seq=0)

    updated_input = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("5"),
        last_journal_seq=1,
    )
    async with pool.acquire() as conn, conn.transaction():
        updated = await repo.upsert(conn, updated_input, expected_seq=0)

    assert updated.quantity == Decimal("5")
    assert updated.last_journal_seq == 1
    assert await _count(pool, position_key) == 1, "replace must keep a single row per key"


async def test_upsert_with_stale_expected_seq_raises_and_does_not_overwrite(pool, repo):
    """DoD: a stale conditional upsert must not overwrite the latest snapshot."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    initial = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("0"),
        last_journal_seq=0,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, initial, expected_seq=0)

    fresh_update = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("5"),
        last_journal_seq=1,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, fresh_update, expected_seq=0)

    stale_update = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("999"),
        last_journal_seq=2,
    )
    with pytest.raises(ConcurrencyConflictError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(conn, stale_update, expected_seq=0)

    async with pool.acquire() as conn, conn.transaction():
        current = await repo.get(conn, tenant_id, position_key)
    assert current is not None
    assert current.quantity == Decimal("5"), "a stale upsert overwrote the latest snapshot"
    assert current.last_journal_seq == 1


async def test_upsert_in_rolled_back_transaction_leaves_previous_version(pool, repo):
    """failure injection -- the caller's transaction fails after `upsert`
    (e.g. a later journal append raises): the DELETE+INSERT replace must roll
    back as a unit, leaving exactly the previous version (and, for a brand-new
    key, no row at all)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)

    class _Injected(RuntimeError):
        pass

    with pytest.raises(_Injected):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                    quantity=Decimal("0"), last_journal_seq=0,
                ),
                expected_seq=0,
            )
            raise _Injected("downstream failure after first creation")
    assert await _count(pool, position_key) == 0

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                quantity=Decimal("1"), last_journal_seq=1,
            ),
            expected_seq=0,
        )
    with pytest.raises(_Injected):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                    quantity=Decimal("7"), last_journal_seq=2,
                ),
                expected_seq=1,
            )
            raise _Injected("downstream failure after replace")
    row = await _row(pool, position_key)
    assert row is not None
    assert row["quantity"] == Decimal("1")
    assert row["last_journal_seq"] == 1
    assert row["portfolio_id"] == default_portfolio_id(tenant_id)
    assert await _count(pool, position_key) == 1


async def test_concurrent_first_creation_raises_concurrency_conflict_not_raw_db_error(pool, repo):
    """FA-10 QA(task-2095) regression: when `_UPSERT_SQL` moved from
    `INSERT ... ON CONFLICT DO UPDATE` to DELETE+INSERT, two concurrent first
    creations (`expected_seq=0`) of the same `position_key` used to leak a raw
    `asyncpg.UniqueViolationError` -- unlike every other conflict shape, which
    gets `ConcurrencyConflictError`. `ON CONFLICT (position_key) DO NOTHING`
    folds that race into the same `ConcurrencyConflictError` path; exactly one
    row must exist afterwards."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)

    # `pg_sleep` before the write is timing-dependent (flaky under load): both
    # transactions must still be uncommitted when the *other* one evaluates
    # `existing`, or the second one takes the legitimate "replace" branch
    # instead of racing the INSERT. An explicit barrier makes the overlap
    # deterministic: both transactions BEGIN, then wait for each other before
    # either issues the upsert statement.
    both_started = asyncio.Event()
    arrivals = 0
    arrivals_lock = asyncio.Lock()

    async def create() -> PositionSnapshotView:
        snapshot = _snapshot(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            quantity=Decimal("0"),
            last_journal_seq=0,
        )
        conn = await pool.acquire()
        try:
            tx = conn.transaction()
            await tx.start()
            nonlocal arrivals
            async with arrivals_lock:
                arrivals += 1
                if arrivals == 2:
                    both_started.set()
            await both_started.wait()
            try:
                result = await repo.upsert(conn, snapshot, expected_seq=0)
            except BaseException:
                await tx.rollback()
                raise
            await tx.commit()
            return result
        finally:
            await pool.release(conn)

    results = await asyncio.gather(create(), create(), return_exceptions=True)

    successes = [r for r in results if isinstance(r, PositionSnapshotView)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrencyConflictError)
    assert not isinstance(failures[0], asyncpg.PostgresError)
    assert await _count(pool, position_key) == 1, "duplicate snapshot key must stay a single row"


async def test_list_open_returns_only_nonzero_quantity_for_tenant_and_account(pool, repo):
    tenant_id, account_id = await _setup(pool)
    open_key = _key(tenant_id)
    closed_key = _key(tenant_id)

    open_snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=open_key,
        quantity=Decimal("3"),
        last_journal_seq=1,
    )
    closed_snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=closed_key,
        quantity=Decimal("0"),
        last_journal_seq=1,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(conn, open_snapshot, expected_seq=0)
        await repo.upsert(conn, closed_snapshot, expected_seq=0)

    async with pool.acquire() as conn, conn.transaction():
        open_positions = await repo.list_open(conn, tenant_id, account_id)

    keys = {s.position_key for s in open_positions}
    assert open_key in keys
    assert closed_key not in keys
