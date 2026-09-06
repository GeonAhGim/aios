"""FA-8 `allocate_order_fills` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(§9 FA-8): 배분 합계 == 체결(FA-A3, 도메인이 이미 강제 — 여기서는 결과가
그대로 반영되는지만 확인), 원장 연결(sub_account별 `MANUAL_ADJUSTMENT` 분개가
실제로 잔액을 바꾸고, 같은 order_id로 재호출해도 REPLAY라 중복 반영되지
않음)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.application.allocate_fills import (
    AllocationTargetError,
    NoFillsError,
    allocate_order_fills,
)
from src.foundation.allocation.domain.policy import AllocationPolicy, WeightTarget
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import SubAccount
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from tests.integration.foundation.allocation.conftest import (
    create_order_with_fills,
    create_test_user,
    wallet_available_balance,
)
from tests.integration.foundation.entities.conftest import build_hierarchy


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _ports(pool):
    return (
        PostgresEntityRepository(pool),
        PostgresJournalRepository(pool),
        PostgresBalanceRepository(pool),
        PostgresAuditEventRepository(pool),
    )


async def test_allocate_order_fills_splits_pro_rata_and_posts_ledger(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    owner_b = await create_test_user(pool)
    sub_account_b = await entities.create_sub_account(
        SubAccount(
            sub_account_id=uuid4(),
            portfolio_id=hierarchy.portfolio.portfolio_id,
            owner_ref=owner_b,
        )
    )

    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("100"), Decimal("150.00")), (Decimal("100"), Decimal("152.00"))],
    )

    async with pool.acquire() as conn, conn.transaction():
        results = await allocate_order_fills(
            conn,
            tenant_id=hierarchy.tenant_id,
            order_id=order_id,
            policy=AllocationPolicy.PRO_RATA,
            weight_targets=(
                WeightTarget(
                    sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                ),
                WeightTarget(sub_account_id=sub_account_b.sub_account_id, weight=Decimal("1")),
            ),
            quantum=Decimal("1"),
            price_quantum=Decimal("0.01"),
            entities=entities,
            journal=journal,
            balances=balances,
            audit=audit,
            clock=_clock,
            trace_id=uuid4(),
        )

    assert len(results) == 2
    assert sum(r.quantity for r in results) == Decimal("200")
    for r in results:
        assert r.quantity == Decimal("100")
        assert r.average_price == Decimal("151.00")
        assert r.replayed is False

    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("15100.00")
    assert await wallet_available_balance(pool, owner_b) == Decimal("15100.00")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sub_account_id, quantity, average_price FROM fill_allocation "
            "WHERE order_id = $1 ORDER BY sub_account_id",
            order_id,
        )
    assert len(rows) == 2


async def test_allocate_order_fills_same_order_twice_does_not_double_post(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _run():
        async with pool.acquire() as conn, conn.transaction():
            return await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )

    first = await _run()
    second = await _run()

    assert first[0].replayed is False
    assert second[0].replayed is True
    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("1000.00")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM fill_allocation WHERE order_id = $1", order_id
        )
    assert count == 1


async def test_allocate_order_fills_no_fills_rejected(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[],
    )

    with pytest.raises(NoFillsError):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(
                    WeightTarget(
                        sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                    ),
                ),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )


async def test_allocate_order_fills_unknown_sub_account_rejected(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )

    with pytest.raises(AllocationTargetError):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(WeightTarget(sub_account_id=uuid4(), weight=Decimal("1")),),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )
