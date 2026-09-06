"""FA-2 — PostgresEntityRepository 실DB 통합테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
— FK·유일성, 교차 테넌트 404(LA-22/PLT-27 선례) 검증. 마이그레이션
왕복은 `test_migration_roundtrip.py`가 별도로 다룬다."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from tests.integration.conftest import create_test_user
from tests.integration.foundation.entities.conftest import build_hierarchy, now_utc


async def test_create_and_get_roundtrip_across_all_four_levels(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    fetched_entity = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    fetched_fund = await repo.get_fund(seeded.tenant_id, seeded.fund.fund_id)
    fetched_portfolio = await repo.get_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id)
    fetched_sub_account = await repo.get_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id
    )

    assert fetched_entity == seeded.legal_entity
    assert fetched_fund == seeded.fund
    assert fetched_portfolio == seeded.portfolio
    assert fetched_sub_account == seeded.sub_account


async def test_get_nonexistent_id_returns_none_at_every_level(pool, repo):
    tenant_id = await create_test_user(pool)
    missing = uuid4()

    assert await repo.get_legal_entity(tenant_id, missing) is None
    assert await repo.get_fund(tenant_id, missing) is None
    assert await repo.get_portfolio(tenant_id, missing) is None
    assert await repo.get_sub_account(tenant_id, missing) is None


async def test_cross_tenant_get_collapses_to_none_at_every_level(pool, repo):
    # 404 동형(LA-22 선례) — 존재는 하지만 다른 tenant 소유인 id도 "없음"과
    # 똑같이 None으로 접혀야 한다. 호출부가 존재 여부와 소유권 여부를
    # 구분할 방법을 구조적으로 없앤다.
    owner = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    assert await repo.get_legal_entity(other_tenant_id, owner.legal_entity.entity_id) is None
    assert await repo.get_fund(other_tenant_id, owner.fund.fund_id) is None
    assert await repo.get_portfolio(other_tenant_id, owner.portfolio.portfolio_id) is None
    assert (
        await repo.get_sub_account(other_tenant_id, owner.sub_account.sub_account_id) is None
    )


async def test_duplicate_primary_key_is_rejected_at_every_level(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_legal_entity(
            LegalEntity(
                entity_id=seeded.legal_entity.entity_id,
                tenant_id=seeded.tenant_id,
                name="dup",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_fund(
            Fund(
                fund_id=seeded.fund.fund_id,
                entity_id=seeded.legal_entity.entity_id,
                base_currency=Currency.USDT,
                inception=date(2026, 1, 1),
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_portfolio(
            Portfolio(
                portfolio_id=seeded.portfolio.portfolio_id,
                fund_id=seeded.fund.fund_id,
                venue_account_ref="dup",
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_sub_account(
            SubAccount(
                sub_account_id=seeded.sub_account.sub_account_id,
                portfolio_id=seeded.portfolio.portfolio_id,
                owner_ref=seeded.tenant_id,
            )
        )


async def test_fund_rejects_reference_to_missing_legal_entity(pool, repo):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_fund(
            Fund(
                fund_id=uuid4(),
                entity_id=uuid4(),
                base_currency=Currency.USDT,
                inception=date(2026, 1, 1),
            )
        )


async def test_portfolio_rejects_reference_to_missing_fund(pool, repo):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_portfolio(
            Portfolio(portfolio_id=uuid4(), fund_id=uuid4(), venue_account_ref="x")
        )


async def test_sub_account_rejects_reference_to_missing_portfolio(pool, repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_sub_account(
            SubAccount(sub_account_id=uuid4(), portfolio_id=uuid4(), owner_ref=tenant_id)
        )


async def test_close_legal_entity_then_reclose_raises_concurrency_conflict(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    closed = await repo.close_legal_entity(
        seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
    )
    assert closed.closed_at is not None

    with pytest.raises(ConcurrencyConflictError):
        await repo.close_legal_entity(
            seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
        )


async def test_close_legal_entity_cross_tenant_raises_lookup_error(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_legal_entity(
            other_tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
        )
    # 실패한 시도가 실제로 폐쇄 상태를 바꾸지 않았는지 확인.
    reread = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_fund_cross_tenant_raises_lookup_error_and_leaves_open(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_fund(other_tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    reread = await repo.get_fund(seeded.tenant_id, seeded.fund.fund_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_portfolio_then_reclose_raises_concurrency_conflict(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    closed = await repo.close_portfolio(
        seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
    )
    assert closed.closed_at is not None

    with pytest.raises(ConcurrencyConflictError):
        await repo.close_portfolio(
            seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
        )


async def test_close_sub_account_cross_tenant_raises_lookup_error(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_sub_account(
            other_tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
        )


async def test_list_children_reflect_created_rows(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    funds = await repo.list_funds_by_entity(seeded.legal_entity.entity_id)
    portfolios = await repo.list_portfolios_by_fund(seeded.fund.fund_id)
    sub_accounts = await repo.list_sub_accounts_by_portfolio(seeded.portfolio.portfolio_id)

    assert [f.fund_id for f in funds] == [seeded.fund.fund_id]
    assert [p.portfolio_id for p in portfolios] == [seeded.portfolio.portfolio_id]
    assert [s.sub_account_id for s in sub_accounts] == [seeded.sub_account.sub_account_id]
