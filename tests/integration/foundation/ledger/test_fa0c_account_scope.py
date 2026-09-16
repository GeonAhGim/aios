"""FA-0c — ledger_account 구조적 스코프 컬럼 통합테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0c
(§9 표 112행). DoD: (1) 문자열 account_code만으로는 서로 다른 두 포트폴리오가
같은 account_type의 계정을 만들 때 UniqueViolation으로 거부됨을 재현,
(2) entity_id/fund_id/portfolio_id 구조적 컬럼을 쓰면 그 두 포트폴리오의
계정 생성이 성공.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1942(commit
4c925cf4)의 D3 하한 미달로 지적한 공백 중 동시성은
`test_concurrent_inserts_for_same_scope_and_type_serialize_to_one_winner`
(task-2474/qa-2474, commit 5c147d58)로 이미 메워졌다. 이 파일은 여기에
적대적 증거(`test_adversarial_null_scope_column_bypasses_structural_unique_constraint`)
를 task-3031에서 추가한다. 리플레이 증거·성능 단언은 순수 도메인 쪽(I/O
없음)이라 tests/foundation/unit/ledger/test_chart_of_accounts.py에 있다.
"""

from __future__ import annotations

import asyncio
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


async def test_concurrent_inserts_for_same_scope_and_type_serialize_to_one_winner(
    pool: asyncpg.Pool,
) -> None:
    """D3: two genuinely concurrent connections race to insert the same
    (entity_id, fund_id, portfolio_id, account_type) tuple. The structural
    UNIQUE constraint must let exactly one commit and reject the other with
    UniqueViolationError -- proving the invariant holds under real concurrency,
    not just sequential calls against a single connection."""
    entity_id, fund_id, portfolio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    account_code_a = f"PORTFOLIO:{portfolio_id}:RACE_A_{uuid.uuid4().hex[:8]}"
    account_code_b = f"PORTFOLIO:{portfolio_id}:RACE_B_{uuid.uuid4().hex[:8]}"

    results = await asyncio.gather(
        _insert_scoped_account(
            pool,
            account_code=account_code_a,
            account_type=AccountType.ASSET,
            entity_id=entity_id,
            fund_id=fund_id,
            portfolio_id=portfolio_id,
        ),
        _insert_scoped_account(
            pool,
            account_code=account_code_b,
            account_type=AccountType.ASSET,
            entity_id=entity_id,
            fund_id=fund_id,
            portfolio_id=portfolio_id,
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if r is None]
    failures = [r for r in results if r is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], asyncpg.UniqueViolationError)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_account "
            "WHERE entity_id = $1 AND fund_id = $2 AND portfolio_id = $3 "
            "AND account_type = 'ASSET'",
            entity_id,
            fund_id,
            portfolio_id,
        )
    assert count == 1


async def test_adversarial_null_scope_column_bypasses_structural_unique_constraint(
    pool: asyncpg.Pool,
) -> None:
    """적대적 -- Postgres composite UNIQUE는 NULL을 서로 다른 값으로 취급한다
    (NULL IS DISTINCT FROM NULL). 마이그레이션 18965d657219의 독스트링이
    명시하듯 `ensure_account`가 만드는 USER/PLATFORM 계정은 entity_id/
    fund_id/portfolio_id를 전부 NULL로 남겨둔다 -- "FA-4/FA-8이 실제
    포트폴리오 배선을 맡는다"는 전제다. 이 테스트는 그 경계를 실DB로
    고정한다: 세 스코프 컬럼 중 단 하나만 NULL이어도(entity_id/fund_id는
    같고 portfolio_id만 NULL인 행) 구조적 UNIQUE(tenant_id, entity_id,
    fund_id, portfolio_id, account_type)는 이미 존재하는 완전한 스코프
    행과 전혀 충돌하지 않고 중복 삽입이 그냥 성공한다. 향후 FA-4/FA-8
    포트폴리오 배선 코드가 세 컬럼 중 하나라도 채우지 않고 지나가면 이
    불변식이 조용히 깨진다는 것을 이 회귀 테스트가 붙잡는다."""
    entity_id, fund_id, portfolio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _insert_scoped_account(
        pool,
        account_code=coa.portfolio_account(portfolio_id, AccountType.LIABILITY),
        account_type=AccountType.LIABILITY,
        entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
    )

    bypass_code = f"PORTFOLIO:NULL_BYPASS_{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account "
            "(account_code, account_type, currency, allow_negative, "
            " tenant_id, entity_id, fund_id, portfolio_id) "
            "VALUES ($1, $2, $3, FALSE, $4, $5, $6, NULL)",
            bypass_code,
            AccountType.LIABILITY.value,
            Currency.KRW.value,
            _TEST_TENANT_ID,
            entity_id,
            fund_id,
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_account "
            "WHERE entity_id = $1 AND fund_id = $2 AND account_type = 'LIABILITY'",
            entity_id,
            fund_id,
        )
    assert count == 2, (
        "NULL portfolio_id가 구조적 UNIQUE의 보호 범위 밖이라는 경계가 바뀌었다 -- "
        "포트폴리오 배선 코드는 반드시 entity_id/fund_id/portfolio_id를 항상 "
        "함께 채워야 한다."
    )
