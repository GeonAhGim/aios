"""FA-5 — `verify_entity_context` hierarchy-link validation tests.

Tests that `verify_entity_context` rejects forged EntityContext where
hierarchy links (fund.entity_id, portfolio.fund_id, sub_account.portfolio_id)
do not match the ctx values, even when all four rows exist and belong
to the same tenant.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

import pytest

from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    verify_entity_context,
)
from src.foundation.entities.contracts.v1 import (
    EntityContext,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _legal_entity(*, entity_id: UUID | None = None, tenant_id: UUID) -> LegalEntity:
    return LegalEntity(
        entity_id=entity_id or uuid4(),
        tenant_id=tenant_id,
        name="Test",
        jurisdiction="KR",
        region_tag="kr",
    )


def _fund(
    *,
    fund_id: UUID | None = None,
    entity_id: UUID | None = None,
    tenant_id: UUID,
) -> Fund:
    return Fund(
        fund_id=fund_id or uuid4(),
        entity_id=entity_id or uuid4(),
        base_currency="USDT",
        inception=datetime.now(UTC).date(),
    )


def _portfolio(
    *,
    portfolio_id: UUID | None = None,
    fund_id: UUID | None = None,
    tenant_id: UUID,
) -> Portfolio:
    return Portfolio(
        portfolio_id=portfolio_id or uuid4(),
        fund_id=fund_id or uuid4(),
        venue_account_ref="v",
    )


def _sub_account(
    *,
    sub_account_id: UUID | None = None,
    portfolio_id: UUID | None = None,
    tenant_id: UUID,
) -> SubAccount:
    return SubAccount(
        sub_account_id=sub_account_id or uuid4(),
        portfolio_id=portfolio_id or uuid4(),
        owner_ref=tenant_id,
    )


class _FakeRepo:
    """In-memory repo for unit tests — returns whatever is seeded."""

    def __init__(self) -> None:
        self.entities: dict[UUID, LegalEntity] = {}
        self.funds: dict[UUID, Fund] = {}
        self.portfolios: dict[UUID, Portfolio] = {}
        self.sub_accounts: dict[UUID, SubAccount] = {}

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        e = self.entities.get(entity_id)
        if e and e.tenant_id == tenant_id:
            return e
        return None

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        return self.funds.get(fund_id)

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        return self.portfolios.get(portfolio_id)

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        return self.sub_accounts.get(sub_account_id)


def _valid_ctx(repo: _FakeRepo) -> EntityContext:
    """Build a fully consistent hierarchy and return ctx."""
    tenant_id = uuid4()
    entity = _legal_entity(tenant_id=tenant_id)
    fund = _fund(fund_id=uuid4(), entity_id=entity.entity_id, tenant_id=tenant_id)
    portfolio = _portfolio(portfolio_id=uuid4(), fund_id=fund.fund_id, tenant_id=tenant_id)
    sub_account = _sub_account(
        sub_account_id=uuid4(), portfolio_id=portfolio.portfolio_id, tenant_id=tenant_id
    )
    repo.entities[entity.entity_id] = entity
    repo.funds[fund.fund_id] = fund
    repo.portfolios[portfolio.portfolio_id] = portfolio
    repo.sub_accounts[sub_account.sub_account_id] = sub_account
    return EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=entity.entity_id,
        fund_id=fund.fund_id,
        portfolio_id=portfolio.portfolio_id,
        sub_account_id=sub_account.sub_account_id,
    )


# ── Negative tests (3+ required by D2) ──────────────────────────────────


async def test_verify_entity_context_rejects_mismatched_fund_entity_id() -> None:
    """Fund.entity_id does not match ctx.legal_entity_id — forged ctx."""
    repo = _FakeRepo()
    tenant_id = uuid4()
    # entity A
    entity_a = _legal_entity(tenant_id=tenant_id)
    # entity B (different)
    entity_b = _legal_entity(tenant_id=tenant_id)
    # fund belongs to entity B, but ctx claims entity A
    fund = _fund(fund_id=uuid4(), entity_id=entity_b.entity_id, tenant_id=tenant_id)
    portfolio = _portfolio(portfolio_id=uuid4(), fund_id=fund.fund_id, tenant_id=tenant_id)
    sub_account = _sub_account(
        sub_account_id=uuid4(), portfolio_id=portfolio.portfolio_id, tenant_id=tenant_id
    )
    repo.entities[entity_a.entity_id] = entity_a
    repo.entities[entity_b.entity_id] = entity_b
    repo.funds[fund.fund_id] = fund
    repo.portfolios[portfolio.portfolio_id] = portfolio
    repo.sub_accounts[sub_account.sub_account_id] = sub_account

    ctx = EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=entity_a.entity_id,  # does NOT match fund.entity_id
        fund_id=fund.fund_id,
        portfolio_id=portfolio.portfolio_id,
        sub_account_id=sub_account.sub_account_id,
    )

    with pytest.raises(EntityContextResolutionError) as exc_info:
        await verify_entity_context(repo, ctx)
    assert "일치하지 않습니다" in str(exc_info.value)


async def test_verify_entity_context_rejects_mismatched_portfolio_fund_id() -> None:
    """Portfolio.fund_id does not match ctx.fund_id — forged ctx."""
    repo = _FakeRepo()
    tenant_id = uuid4()
    entity = _legal_entity(tenant_id=tenant_id)
    fund_a = _fund(fund_id=uuid4(), entity_id=entity.entity_id, tenant_id=tenant_id)
    fund_b = _fund(fund_id=uuid4(), entity_id=entity.entity_id, tenant_id=tenant_id)
    # portfolio belongs to fund_b, but ctx claims fund_a
    portfolio = _portfolio(portfolio_id=uuid4(), fund_id=fund_b.fund_id, tenant_id=tenant_id)
    sub_account = _sub_account(
        sub_account_id=uuid4(), portfolio_id=portfolio.portfolio_id, tenant_id=tenant_id
    )
    repo.entities[entity.entity_id] = entity
    repo.funds[fund_a.fund_id] = fund_a
    repo.funds[fund_b.fund_id] = fund_b
    repo.portfolios[portfolio.portfolio_id] = portfolio
    repo.sub_accounts[sub_account.sub_account_id] = sub_account

    ctx = EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=entity.entity_id,
        fund_id=fund_a.fund_id,  # does NOT match portfolio.fund_id
        portfolio_id=portfolio.portfolio_id,
        sub_account_id=sub_account.sub_account_id,
    )

    with pytest.raises(EntityContextResolutionError) as exc_info:
        await verify_entity_context(repo, ctx)
    assert "일치하지 않습니다" in str(exc_info.value)


async def test_verify_entity_context_rejects_mismatched_sub_account_portfolio_id() -> None:
    """SubAccount.portfolio_id does not match ctx.portfolio_id — forged ctx."""
    repo = _FakeRepo()
    tenant_id = uuid4()
    entity = _legal_entity(tenant_id=tenant_id)
    fund = _fund(fund_id=uuid4(), entity_id=entity.entity_id, tenant_id=tenant_id)
    portfolio_a = _portfolio(portfolio_id=uuid4(), fund_id=fund.fund_id, tenant_id=tenant_id)
    portfolio_b = _portfolio(portfolio_id=uuid4(), fund_id=fund.fund_id, tenant_id=tenant_id)
    # sub_account belongs to portfolio_b, but ctx claims portfolio_a
    sub_account = _sub_account(
        sub_account_id=uuid4(), portfolio_id=portfolio_b.portfolio_id, tenant_id=tenant_id
    )
    repo.entities[entity.entity_id] = entity
    repo.funds[fund.fund_id] = fund
    repo.portfolios[portfolio_a.portfolio_id] = portfolio_a
    repo.portfolios[portfolio_b.portfolio_id] = portfolio_b
    repo.sub_accounts[sub_account.sub_account_id] = sub_account

    ctx = EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=entity.entity_id,
        fund_id=fund.fund_id,
        portfolio_id=portfolio_a.portfolio_id,  # does NOT match sub_account.portfolio_id
        sub_account_id=sub_account.sub_account_id,
    )

    with pytest.raises(EntityContextResolutionError) as exc_info:
        await verify_entity_context(repo, ctx)
    assert "일치하지 않습니다" in str(exc_info.value)


# ── Failure injection test (1 required by D2) ───────────────────────────


async def test_verify_entity_context_propagates_connection_error() -> None:
    """DB connection error during lookup should propagate, not be swallowed."""

    class _ConnErrorRepo:
        async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> NoReturn:
            raise ConnectionError("simulated connection failure")

        async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> NoReturn:
            raise AssertionError("should not reach get_fund")

        async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> NoReturn:
            raise AssertionError("should not reach get_portfolio")

        async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> NoReturn:
            raise AssertionError("should not reach get_sub_account")

    ctx = _valid_ctx(_FakeRepo())  # structure only — repo will raise anyway
    with pytest.raises(ConnectionError) as exc_info:
        await verify_entity_context(_ConnErrorRepo(), ctx)
    assert "simulated connection failure" in str(exc_info.value)


# ── Positive test (1 required by D2) ────────────────────────────────────


async def test_verify_entity_context_passes_valid_hierarchy() -> None:
    """All 4 rows exist, open, and links are consistent — no exception."""
    repo = _FakeRepo()
    ctx = _valid_ctx(repo)
    # Should not raise
    await verify_entity_context(repo, ctx)


# ── Red-gate reproduction: same-tenant forged ctx that passed before fix ─


async def test_verify_entity_context_red_gate_same_tenant_forge() -> None:
    """Red-gate: same-tenant forged ctx that would have passed verify_entity_context
    before this hierarchy-link check was added. All 4 rows exist and are open,
    but the link between fund.entity_id and ctx.legal_entity_id is broken.

    Before the fix: _require_open passes for all 4 rows, verify_entity_context
    returns None (silent acceptance). After the fix: EntityContextResolutionError.
    """
    repo = _FakeRepo()
    tenant_id = uuid4()

    # Create two legal entities in the same tenant
    entity_a = _legal_entity(entity_id=uuid4(), tenant_id=tenant_id)
    entity_b = _legal_entity(entity_id=uuid4(), tenant_id=tenant_id)

    # Fund belongs to entity_b
    fund = _fund(fund_id=uuid4(), entity_id=entity_b.entity_id, tenant_id=tenant_id)
    portfolio = _portfolio(portfolio_id=uuid4(), fund_id=fund.fund_id, tenant_id=tenant_id)
    sub_account = _sub_account(
        sub_account_id=uuid4(), portfolio_id=portfolio.portfolio_id, tenant_id=tenant_id
    )

    # Store all rows
    repo.entities[entity_a.entity_id] = entity_a
    repo.entities[entity_b.entity_id] = entity_b
    repo.funds[fund.fund_id] = fund
    repo.portfolios[portfolio.portfolio_id] = portfolio
    repo.sub_accounts[sub_account.sub_account_id] = sub_account

    # Forge ctx: claims entity_a as root, but fund actually belongs to entity_b
    forged_ctx = EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=entity_a.entity_id,  # wrong root
        fund_id=fund.fund_id,
        portfolio_id=portfolio.portfolio_id,
        sub_account_id=sub_account.sub_account_id,
    )

    with pytest.raises(EntityContextResolutionError):
        await verify_entity_context(repo, forged_ctx)
