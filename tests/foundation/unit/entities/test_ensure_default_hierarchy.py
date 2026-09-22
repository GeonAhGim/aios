"""`application/ensure_default_hierarchy.py` unit tests -- fake repository, no DB.

task-771991202 (FA-0d-fix): the shared bootstrap must be idempotent and must
only create the levels that are missing, because both the test-tenant seed and
the operational path call it without knowing what already exists.

task-4889 (DEEPEN, QA-2405 follow-up, CTO decision (a)): `ensure_default_hierarchy`
is two round trips (get, then create) per level, not one atomic step, so two
concurrent bootstraps for the same `user_id` can both observe a level missing
and race to create it. The tests below cover that race (`_get_or_create`
recovering via `ConcurrencyConflictError`), the negative/failure-injection
cases for when recovery itself cannot find a winner, and a throughput bound
on the happy path.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.entities.application.ensure_default_hierarchy import (
    ensure_default_hierarchy,
)
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
    default_sub_account_id,
)


@dataclass
class _FakeRepository:
    legal_entities: dict[UUID, LegalEntity] = field(default_factory=dict)
    funds: dict[UUID, Fund] = field(default_factory=dict)
    portfolios: dict[UUID, Portfolio] = field(default_factory=dict)
    sub_accounts: dict[UUID, SubAccount] = field(default_factory=dict)
    creates: list[str] = field(default_factory=list)
    get_calls: int = 0

    def _tenant_of_entity(self, entity_id: UUID) -> UUID | None:
        entity = self.legal_entities.get(entity_id)
        return None if entity is None else entity.tenant_id

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        self.get_calls += 1
        entity = self.legal_entities.get(entity_id)
        return entity if entity is not None and entity.tenant_id == tenant_id else None

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity:
        assert entity.entity_id not in self.legal_entities, "duplicate legal_entity"
        self.legal_entities[entity.entity_id] = entity
        self.creates.append("legal_entity")
        return entity

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        self.get_calls += 1
        fund = self.funds.get(fund_id)
        if fund is None or self._tenant_of_entity(fund.entity_id) != tenant_id:
            return None
        return fund

    async def create_fund(self, fund: Fund) -> Fund:
        assert fund.fund_id not in self.funds, "duplicate fund"
        self.funds[fund.fund_id] = fund
        self.creates.append("fund")
        return fund

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        self.get_calls += 1
        portfolio = self.portfolios.get(portfolio_id)
        if portfolio is None or await self.get_fund(tenant_id, portfolio.fund_id) is None:
            return None
        return portfolio

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio:
        assert portfolio.portfolio_id not in self.portfolios, "duplicate portfolio"
        self.portfolios[portfolio.portfolio_id] = portfolio
        self.creates.append("portfolio")
        return portfolio

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        self.get_calls += 1
        sub = self.sub_accounts.get(sub_account_id)
        if sub is None or await self.get_portfolio(tenant_id, sub.portfolio_id) is None:
            return None
        return sub

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount:
        assert sub_account.sub_account_id not in self.sub_accounts, "duplicate sub_account"
        self.sub_accounts[sub_account.sub_account_id] = sub_account
        self.creates.append("sub_account")
        return sub_account


async def _ensure(repo: _FakeRepository, user_id: UUID, *, venue_account_ref: str = "venue-1"):
    return await ensure_default_hierarchy(
        repo,
        user_id=user_id,
        tenant_id=user_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref=venue_account_ref,
        inception=date(2026, 1, 1),
    )


async def test_creates_all_four_levels_with_deterministic_ids_when_nothing_exists():
    repo = _FakeRepository()
    user_id = uuid4()

    hierarchy = await _ensure(repo, user_id)

    assert repo.creates == ["legal_entity", "fund", "portfolio", "sub_account"]
    assert hierarchy.legal_entity.entity_id == default_entity_id(user_id)
    assert hierarchy.fund.fund_id == default_fund_id(user_id)
    assert hierarchy.portfolio.portfolio_id == default_portfolio_id(user_id)
    assert hierarchy.sub_account.sub_account_id == default_sub_account_id(user_id)
    assert hierarchy.portfolio.fund_id == hierarchy.fund.fund_id


async def test_second_call_is_a_no_op_and_returns_the_persisted_rows():
    repo = _FakeRepository()
    user_id = uuid4()
    first = await _ensure(repo, user_id, venue_account_ref="venue-first")

    second = await _ensure(repo, user_id, venue_account_ref="venue-second")

    assert repo.creates == ["legal_entity", "fund", "portfolio", "sub_account"]
    assert second == first
    # persisted attributes win over the arguments of a later call
    assert second.portfolio.venue_account_ref == "venue-first"


async def test_partial_hierarchy_only_creates_the_missing_levels():
    repo = _FakeRepository()
    user_id = uuid4()
    complete = await _ensure(repo, user_id)
    # simulate a bootstrap that stopped after the fund (portfolio/sub_account missing)
    del repo.portfolios[complete.portfolio.portfolio_id]
    del repo.sub_accounts[complete.sub_account.sub_account_id]
    repo.creates.clear()

    hierarchy = await _ensure(repo, user_id)

    assert repo.creates == ["portfolio", "sub_account"]
    assert hierarchy.legal_entity == complete.legal_entity
    assert hierarchy.fund == complete.fund
    assert hierarchy.portfolio.portfolio_id == default_portfolio_id(user_id)


async def test_two_users_get_disjoint_hierarchies():
    repo = _FakeRepository()
    a = await _ensure(repo, uuid4())
    b = await _ensure(repo, uuid4())

    assert a.portfolio.portfolio_id != b.portfolio.portfolio_id
    assert len(repo.portfolios) == 2


@dataclass
class _RacyRepository(_FakeRepository):
    """`_FakeRepository` with a real suspension point in each `create_*`, so
    two coroutines racing `ensure_default_hierarchy` for the *same* user on
    one event loop interleave the way two concurrent requests would: both
    `get_*` calls observe the level missing, then both `create_*` calls run,
    and only the second one hits the (simulated) unique-constraint race."""

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity:
        await asyncio.sleep(0)
        if entity.entity_id in self.legal_entities:
            raise ConcurrencyConflictError(f"legal_entity.entity_id={entity.entity_id}")
        return await super().create_legal_entity(entity)

    async def create_fund(self, fund: Fund) -> Fund:
        await asyncio.sleep(0)
        if fund.fund_id in self.funds:
            raise ConcurrencyConflictError(f"fund.fund_id={fund.fund_id}")
        return await super().create_fund(fund)

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio:
        await asyncio.sleep(0)
        if portfolio.portfolio_id in self.portfolios:
            raise ConcurrencyConflictError(f"portfolio.portfolio_id={portfolio.portfolio_id}")
        return await super().create_portfolio(portfolio)

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount:
        await asyncio.sleep(0)
        if sub_account.sub_account_id in self.sub_accounts:
            raise ConcurrencyConflictError(
                f"sub_account.sub_account_id={sub_account.sub_account_id}"
            )
        return await super().create_sub_account(sub_account)


@dataclass
class _AlwaysConflictRepository(_FakeRepository):
    """Every `create_*` raises `ConcurrencyConflictError` and no row is ever
    actually persisted -- models a genuine failure (e.g. a poisoned
    connection or a constraint violation unrelated to a same-id race), as
    opposed to `_RacyRepository`'s "someone else already won" case."""

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity:
        raise ConcurrencyConflictError(f"legal_entity.entity_id={entity.entity_id}")


async def test_concurrent_bootstrap_for_same_user_converges_on_one_winner():
    """D3 adversarial / gate-red reproduction (task-4889, QA-2405 follow-up):
    before `_get_or_create` existed, the loser of the create-vs-create race
    let `ConcurrencyConflictError` propagate straight out of
    `ensure_default_hierarchy`, so one of the two concurrent callers below
    would fail instead of converging on the winner's row. Red before the fix,
    green after (confirmed by temporarily reverting to the plain
    get-then-create body: this test fails with an unhandled
    `ConcurrencyConflictError`, exactly QA-2405's reported failure mode).

    INVARIANTS.md cross-check: no I-0x entry addresses entity-hierarchy
    bootstrap concurrency directly (N/A(no matching invariant) for I-01..09,
    I-11). Closest is I-10 ("implemented != working" -- safety-adjacent
    components need a wiring-proof adversarial test): this bootstrap gates
    every position write's `portfolio_id` FK (module docstring above), so
    this test is that wiring proof for the retry-recovery path.
    """
    repo = _RacyRepository()
    user_id = uuid4()

    first, second = await asyncio.gather(_ensure(repo, user_id), _ensure(repo, user_id))

    assert first == second
    assert repo.creates.count("legal_entity") == 1
    assert repo.creates.count("fund") == 1
    assert repo.creates.count("portfolio") == 1
    assert repo.creates.count("sub_account") == 1
    assert len(repo.legal_entities) == 1
    assert len(repo.portfolios) == 1


async def test_concurrent_bootstrap_for_different_users_does_not_cross_contaminate():
    repo = _RacyRepository()
    user_a, user_b = uuid4(), uuid4()

    a, b = await asyncio.gather(_ensure(repo, user_a), _ensure(repo, user_b))

    assert a.portfolio.portfolio_id != b.portfolio.portfolio_id
    assert len(repo.legal_entities) == 2
    assert len(repo.portfolios) == 2


async def test_race_recovery_costs_at_most_one_extra_get_per_level():
    """Numeric performance assertion: recovering from a lost create-vs-create
    race must cost exactly one extra `get_*` re-query per level, not an
    unbounded retry loop -- `_get_or_create` re-queries once and then
    re-raises if that still does not find a winner (see the failure-injection
    test below), so N concurrent racers for one user cost at most
    N get-calls per level up front plus 1 recovery get for every loser."""
    repo = _RacyRepository()
    user_id = uuid4()
    racers = 5

    started = time.perf_counter()
    await asyncio.gather(*(_ensure(repo, user_id) for _ in range(racers)))
    elapsed = time.perf_counter() - started

    # Each racer does at most 2 get_* calls per level (the initial lookup plus
    # one recovery re-query), and get_portfolio/get_sub_account additionally
    # fan out into their parent's get_* for the tenant check -- worst case
    # that is still linear in (racers * levels), not exponential, so a
    # generous constant catches a retry storm/livelock without hand-deriving
    # the exact fan-out count.
    assert repo.get_calls < 20 * racers
    assert elapsed < 1.0, f"in-memory bootstrap of {racers} racers took {elapsed:.3f}s"


async def test_get_or_create_reraises_conflict_when_no_winner_is_ever_found():
    """Negative / failure-injection test: if `create_*` raises
    `ConcurrencyConflictError` but the row still is not visible afterwards,
    that is not actually a create-vs-create race (the two would agree on the
    same deterministic id) -- it is a genuine failure, and swallowing it
    would silently return a hierarchy that was never persisted. Fail closed
    instead: propagate the error."""
    repo = _AlwaysConflictRepository()
    user_id = uuid4()

    with pytest.raises(ConcurrencyConflictError):
        await _ensure(repo, user_id)

    assert repo.legal_entities == {}


async def test_ensure_default_hierarchy_does_not_swallow_unrelated_lookup_errors():
    """Negative test: a lookup failure that is not a `ConcurrencyConflictError`
    (e.g. a repository bug or an unrelated driver error) must propagate
    unchanged -- `_get_or_create` only special-cases the 105-standard
    conflict error, nothing else."""

    class _BrokenGetRepository(_FakeRepository):
        async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
            raise RuntimeError("fund lookup exploded")

    repo = _BrokenGetRepository()
    user_id = uuid4()

    with pytest.raises(RuntimeError, match="fund lookup exploded"):
        await _ensure(repo, user_id)


async def test_losing_racer_returns_the_winners_persisted_attributes_not_its_own():
    """Negative test: the loser of the race must not silently keep acting on
    its own (unpersisted) argument values -- it has to return the winner's
    actually-persisted row, the same way a sequential second call does
    (see test_second_call_is_a_no_op_and_returns_the_persisted_rows)."""
    repo = _RacyRepository()
    user_id = uuid4()

    first, second = await asyncio.gather(
        _ensure(repo, user_id, venue_account_ref="venue-a"),
        _ensure(repo, user_id, venue_account_ref="venue-b"),
    )

    assert first.portfolio.venue_account_ref == second.portfolio.venue_account_ref
    assert first.portfolio.venue_account_ref in ("venue-a", "venue-b")
