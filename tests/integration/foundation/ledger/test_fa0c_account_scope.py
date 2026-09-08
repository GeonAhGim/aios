"""FA-0c — ledger_account 구조적 스코프 컬럼 통합테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0c
(§9 표 112행). DoD: (1) 문자열 account_code만으로는 서로 다른 두 포트폴리오가
같은 account_type의 계정을 만들 때 UniqueViolation으로 거부됨을 재현,
(2) entity_id/fund_id/portfolio_id 구조적 컬럼을 쓰면 그 두 포트폴리오의
계정 생성이 성공.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import AccountType
from src.foundation.ledger.domain import chart_of_accounts as coa

# `ledger_account.tenant_id` FKs `users(user_id)` — reuse the pre-seeded house
# user (`e7f8a9b0c1d2_wallet_ledger.py`) so these inserts don't need a fresh
# `users` row of their own.
_TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
async def _cleanup_portfolio_test_accounts(pool: asyncpg.Pool):
    """Every row this file inserts uses the `PORTFOLIO:` prefix (real seed rows
    are `USER:`/`PLATFORM:`) — delete them after each test so a leftover row
    doesn't break other suites that replay the full migration chain against the
    shared TEST_DATABASE_URL (e.g. test_migration_fa4_worm_no_backfill.py)."""
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM ledger_account WHERE account_code LIKE 'PORTFOLIO:%'")


async def _insert_scoped_account(
    pool: asyncpg.Pool,
    *,
    account_code: str,
    account_type: AccountType,
    entity_id: uuid.UUID,
    fund_id: uuid.UUID,
    portfolio_id: uuid.UUID,
    tenant_id: uuid.UUID = _TEST_TENANT_ID,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account "
            "(account_code, account_type, currency, allow_negative, "
            " tenant_id, entity_id, fund_id, portfolio_id) "
            "VALUES ($1, $2, $3, FALSE, $4, $5, $6, $7)",
            account_code,
            account_type.value,
            Currency.KRW.value,
            tenant_id,
            entity_id,
            fund_id,
            portfolio_id,
        )


async def test_naive_shared_account_code_across_portfolios_collides(pool: asyncpg.Pool) -> None:
    """Reproduces the pre-FA-0c defect: an account_code that does not embed the
    portfolio identity collides across portfolios under the table's pre-existing
    `UNIQUE(account_code)` constraint — this is why the string grammar alone
    cannot carry portfolio scope."""
    shared_code = f"PORTFOLIO:TEST_NAIVE_{uuid.uuid4().hex[:16].upper()}"
    entity_id, fund_id = uuid.uuid4(), uuid.uuid4()

    await _insert_scoped_account(
        pool,
        account_code=shared_code,
        account_type=AccountType.ASSET,
        entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=uuid.uuid4(),
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_scoped_account(
            pool,
            account_code=shared_code,
            account_type=AccountType.ASSET,
            entity_id=entity_id,
            fund_id=fund_id,
            portfolio_id=uuid.uuid4(),
        )


async def test_two_portfolios_same_account_type_succeeds(pool: asyncpg.Pool) -> None:
    """The fix: two different portfolios can each hold an ASSET account — their
    identity is the (entity_id, fund_id, portfolio_id, account_type) columns,
    not a hand-rolled account_code string."""
    entity_id, fund_id = uuid.uuid4(), uuid.uuid4()
    portfolio_a, portfolio_b = uuid.uuid4(), uuid.uuid4()

    await _insert_scoped_account(
        pool,
        account_code=coa.portfolio_account(portfolio_a, AccountType.ASSET),
        account_type=AccountType.ASSET,
        entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_a,
    )
    await _insert_scoped_account(
        pool,
        account_code=coa.portfolio_account(portfolio_b, AccountType.ASSET),
        account_type=AccountType.ASSET,
        entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_b,
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT portfolio_id FROM ledger_account "
            "WHERE entity_id = $1 AND fund_id = $2 AND account_type = 'ASSET'",
            entity_id,
            fund_id,
        )
    assert {row["portfolio_id"] for row in rows} == {portfolio_a, portfolio_b}


async def test_same_portfolio_duplicate_account_type_still_rejected(pool: asyncpg.Pool) -> None:
    """The new structural UNIQUE must still reject a true duplicate: the same
    portfolio cannot get two ASSET accounts."""
    entity_id, fund_id, portfolio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _insert_scoped_account(
        pool,
        account_code=coa.portfolio_account(portfolio_id, AccountType.ASSET),
        account_type=AccountType.ASSET,
        entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_scoped_account(
            pool,
            account_code=f"PORTFOLIO:{portfolio_id}:ASSET_DUP_{uuid.uuid4().hex[:8]}",
            account_type=AccountType.ASSET,
            entity_id=entity_id,
            fund_id=fund_id,
            portfolio_id=portfolio_id,
        )


async def test_backfilled_seed_accounts_share_house_scope(pool: asyncpg.Pool) -> None:
    """Migration 18965d657219 backfills every pre-existing row via
    `chart_of_accounts.default_scope()` — the 4 PLATFORM:* seed accounts and the
    house USER:*:AVAILABLE account must all resolve to the same
    (entity_id, fund_id, portfolio_id) triple, disambiguated only by
    account_type (they predate any real portfolio)."""
    seed_codes = [
        coa.PLATFORM_CASH_CLEARING,
        coa.PLATFORM_COMMISSION_REVENUE,
        coa.PLATFORM_REFUND_RESERVE,
        coa.PLATFORM_PAYOUT_CLEARING,
        "USER:00000000-0000-0000-0000-000000000001:AVAILABLE",
    ]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT account_code, entity_id, fund_id, portfolio_id FROM ledger_account "
            "WHERE account_code = ANY($1::text[])",
            seed_codes,
        )

    assert len(rows) == 5
    scopes = {(row["entity_id"], row["fund_id"], row["portfolio_id"]) for row in rows}
    assert len(scopes) == 1
    (entity_id, fund_id, portfolio_id) = next(iter(scopes))
    assert None not in (entity_id, fund_id, portfolio_id)
