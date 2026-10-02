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


# ---------------------------------------------------------------------------
# get() tests
# ---------------------------------------------------------------------------


async def test_get_returns_none_when_absent(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, uuid.uuid4(), f"missing:{uuid.uuid4().hex}") is None


async def test_get_returns_none_for_wrong_tenant(pool, repo):
    """task-489/LB-18: even if `position_key` exists, a different owner
    `tenant_id` gets `None` -- indistinguishable from absent (no existence leak)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                quantity=Decimal("5"),
                last_journal_seq=1,
            ),
            expected_seq=0,
        )
    wrong_tenant = uuid.uuid4()
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, wrong_tenant, position_key) is None


# ---------------------------------------------------------------------------
# upsert() -- basic creation / round-trip
# ---------------------------------------------------------------------------


async def test_upsert_creates_row_on_first_call_with_expected_seq_zero(pool, repo):
    """task-771991202: first upsert on a fresh key creates a row with
    `last_journal_seq=0` (the initial seq before any journal entry)."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                quantity=Decimal("10"),
                last_journal_seq=0,
            ),
            expected_seq=0,
        )
    row = await _row(pool, position_key)
    assert row is not None
    assert row["quantity"] == Decimal("10")
    assert row["last_journal_seq"] == 0
    assert row["tenant_id"] == tenant_id


async def test_upsert_writes_portfolio_id_and_fund_id_columns_from_position_key(pool, repo):
    """FA-0d-fix (task-771991202): before the fix the adapter left
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
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                quantity=Decimal("0"),
                last_journal_seq=0,
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
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                quantity=Decimal("2"),
                last_journal_seq=1,
            ),
            expected_seq=0,
        )
    replaced = await _row(pool, position_key)
    assert replaced is not None
    assert replaced["last_journal_seq"] == 1
    assert replaced["portfolio_id"] == default_portfolio_id(tenant_id)
    assert replaced["fund_id"] == default_fund_id(tenant_id)


async def test_upsert_preserves_full_decimal_precision_and_lots_round_trip(pool, repo):
    """수치 round-trip 단언(DEEPEN task-2945): `quantity`/`avg_cost`/
    `realized_pnl_base`/`unrealized_pnl_base`/`fees_base`/`funding_base`/
    `mark_price`는 NUMERIC(30,10) 컬럼이고 `lots`는 JSONB(Decimal이
    문자열로 직렬화됨)다 — 정수부 다자릿수 + 소수부 10자리(음수 포함) 값이
    INSERT...RETURNING 경로(`upsert`가 반환하는 뷰)와 별도 SELECT 경로
    (`get`) 양쪽에서 원본 Decimal과 정확히 일치해야 한다."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id)
    quantity = Decimal("123456789012345.1234567890")
    avg_cost = Decimal("-987654321098765.9876543211")
    mark_price = Decimal("111111111111111.1111111111")
    lot = Lot(quantity=quantity, unit_cost=avg_cost, opened_at=datetime.now(timezone.utc))
    snapshot = _snapshot(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=quantity,
        last_journal_seq=0,
        avg_cost=Money(amount=avg_cost, currency=Currency.KRW),
        lots=[lot],
        realized_pnl_base=Decimal("-0.0000000001"),
        unrealized_pnl_base=Decimal("0.0000000001"),
        fees_base=Decimal("999999999999999.9999999999"),
        funding_base=Decimal("-999999999999999.9999999999"),
        mark_price=Money(amount=mark_price, currency=Currency.KRW),
        mark_at=datetime.now(timezone.utc),
    )

    async with pool.acquire() as conn, conn.transaction():
        created = await repo.upsert(conn, snapshot, expected_seq=0)

    assert created.quantity == quantity
    assert created.avg_cost.amount == avg_cost
    assert created.realized_pnl_base == snapshot.realized_pnl_base
    assert created.unrealized_pnl_base == snapshot.unrealized_pnl_base
    assert created.fees_base == snapshot.fees_base
    assert created.funding_base == snapshot.funding_base
    assert created.mark_price is not None and created.mark_price.amount == mark_price
    assert len(created.lots) == 1
    assert created.lots[0].quantity == quantity
    assert created.lots[0].unit_cost == avg_cost

    async with pool.acquire() as conn, conn.transaction():
        fetched = await repo.get(conn, tenant_id, position_key)
    assert fetched is not None
    assert fetched.quantity == quantity
    assert fetched.avg_cost.amount == avg_cost
    assert fetched.fees_base == snapshot.fees_base
    assert fetched.funding_base == snapshot.funding_base
    assert len(fetched.lots) == 1
    assert fetched.lots[0].unit_cost == avg_cost


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
                        tenant_id=tenant_id,
                        account_id=account_id,
                        position_key=bad_key,
                        quantity=Decimal("0"),
                        last_journal_seq=0,
                    ),
                    expected_seq=0,
                )
        assert await _count(pool, bad_key) == 0


async def test_upsert_rejects_portfolio_that_was_never_bootstrapped(pool, repo):
    """negative -- missing portfolio => clear port-level error, not an FK
    violation from the driver, and no row is written."""
    tenant_id, account_id = await _setup(pool)
    position_key = _key(tenant_id, portfolio_id=uuid.uuid4())
    with pytest.raises(SnapshotPortfolioNotFoundError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    position_key=position_key,
                    quantity=Decimal("1"),
                    last_journal_seq=0,
                ),
                expected_seq=0,
            )
    assert await _count(pool, position_key) == 0


async def test_upsert_rejects_cross_tenant_portfolio_and_leaves_victim_row_untouched(pool, repo):
    """negative -- a portfolio belongs to exactly one tenant. A different
    tenant must not be able to write into it (cross-tenant isolation).

    Also verifies that the rejection is atomic: the victim row is left
    completely untouched."""
    tenant_a, account_a = await _setup(pool)
    tenant_b, account_b = await _setup(pool)

    # Bootstrap a portfolio for tenant_a
    position_key = _key(tenant_a)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert(
            conn,
            _snapshot(
                tenant_id=tenant_a,
                account_id=account_a,
                position_key=position_key,
                quantity=Decimal("100"),
                last_journal_seq=10,
            ),
            expected_seq=0,
        )
    victim_before = await _row(pool, position_key)
    assert victim_before is not None
    assert victim_before["quantity"] == Decimal("100")
    assert victim_before["last_journal_seq"] == 10

    # tenant_b tries to write using tenant_a's portfolio
    attacker_key = _key(tenant_a, portfolio_id=default_portfolio_id(tenant_a))
    with pytest.raises(SnapshotPortfolioTenantMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert(
                conn,
                _snapshot(
                    tenant_id=tenant_b,
                    account_id=account_b,
                    position_key=attacker_key,
                    quantity=Decimal("1"),
                    last_journal_seq=0,
                ),
                expected_seq=0,
            )
    victim_after = await _row(pool, position_key)
    assert victim_after["quantity"] == Decimal("100")
    assert victim_after["last_journal_seq"] == 10


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
