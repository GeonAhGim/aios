"""PostgresSnapshotRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-9.
DoD(task-375): "조건부 upsert 충돌 시 오래된 스냅샷이 최신을 덮어쓰지 않음"
(negative — stale `expected_seq`는 거부되고 최신 값이 유지돼야 한다).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, Lot, PositionSnapshotView
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account


@pytest.fixture
def repo(pool):
    return PostgresSnapshotRepository(pool)


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


async def test_get_returns_none_when_absent(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, uuid.uuid4(), f"missing:{uuid.uuid4().hex}") is None


async def test_get_returns_none_for_wrong_tenant(pool, repo):
    """task-489/LB-18: `position_key`는 실재해도 소유 `tenant_id`가 다르면
    조회 자체가 `None` — 없는 것과 구분하지 않는다(존재 비노출)."""
    tenant_id, account_id = await _setup(pool)
    position_key = f"pos:{uuid.uuid4().hex}"
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
    position_key = f"pos:{uuid.uuid4().hex}"
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


async def test_upsert_with_matching_expected_seq_updates_row(pool, repo):
    tenant_id, account_id = await _setup(pool)
    position_key = f"pos:{uuid.uuid4().hex}"
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


async def test_upsert_with_stale_expected_seq_raises_and_does_not_overwrite(pool, repo):
    """DoD: 조건부 upsert 충돌 시 오래된 스냅샷이 최신을 덮어쓰지 않음."""
    tenant_id, account_id = await _setup(pool)
    position_key = f"pos:{uuid.uuid4().hex}"
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
    assert current.quantity == Decimal("5"), "오래된 upsert가 최신 스냅샷을 덮어썼습니다"
    assert current.last_journal_seq == 1


async def test_concurrent_first_creation_raises_concurrency_conflict_not_raw_db_error(pool, repo):
    """FA-10 QA(task-2095) 회귀: `_UPSERT_SQL`이 `INSERT ... ON CONFLICT DO
    UPDATE`에서 DELETE+INSERT로 바뀌면서, 같은 `position_key`에 대한 두
    동시 최초 생성(`expected_seq=0`)이 (수정 전에는) `asyncpg.
    UniqueViolationError`를 그대로 새 나가게 했다 — 다른 모든 충돌 형태가
    받는 `ConcurrencyConflictError`와 달리 호출자가 잡을 수 없는 드라이버
    예외였다. `ON CONFLICT (position_key) DO NOTHING`을 추가해 이 경합도
    같은 `ConcurrencyConflictError` 경로로 접히는지 검증한다."""
    tenant_id, account_id = await _setup(pool)
    position_key = f"pos:{uuid.uuid4().hex}"

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


async def test_list_open_returns_only_nonzero_quantity_for_tenant_and_account(pool, repo):
    tenant_id, account_id = await _setup(pool)
    open_key = f"pos:{uuid.uuid4().hex}"
    closed_key = f"pos:{uuid.uuid4().hex}"

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
