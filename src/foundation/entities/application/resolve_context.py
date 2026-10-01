"""FA-5 — `application/resolve_context.py`: single context-resolution entry
point for order/position/ledger writes.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5
(§9 FA-5 DoD, §2.1 application row "resolve_context(request) is the single
entry for all writes").

No new resolution rules are defined (PM decision) — id derivation reuses the
deterministic rule from FA-1 `domain/defaults.py` (UUIDv5 keyed only on
user_id) verbatim, and existence/closedness checks reuse the lookup methods
from FA-2 `adapters/postgres_repository.py` (or any repository satisfying the
same partial contract). The only thing this module defines new is the
composition rule: "wrap those results fail-closed into one
`EntityContext`".

Only the FA-1 default layer (personal single-account UX) where
`tenant_id == user_id` is supported now — multi-fund UX where the user
explicitly selects fund/portfolio will be added later as a separate request
shape (field addition to `ResolveContextRequest`, MINOR bump).

On resolution failure (not bootstrapped / closed / cross-tenant), the module
raises `EntityContextResolutionError` instead of guessing or defaulting — the
caller (order/position/ledger write entry points) must not proceed with that
write once it receives this exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from src.foundation.entities.contracts.v1 import (
    EntityContext,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
    default_sub_account_id,
)


class EntityContextResolutionError(ValueError):
    """FA-5 fail-closed — raises this exception (without guessing a value)
    when any of the 4 entity hierarchy layers is missing (not bootstrapped)
    or closed. Reuses the same exception when a write entry point (order,
    position, ledger) receives no `entity_context` at all (e.g., someone
    passed a literal `None` to bypass static checks) — because "no context"
    always leads to the same caller action (reject the write), regardless of
    whether the root cause is resolution failure or missing propagation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class EntityRepository(Protocol):
    """Partial contract that `PostgresEntityRepository` (FA-2) satisfies —
    only 4 lookup methods are needed (creation/closure is not this use case's
    responsibility)."""

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None: ...

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None: ...

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None: ...

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None: ...


@dataclass(frozen=True)
class ResolveContextRequest:
    """Request for the personal-user default layer (FA-1) — given only
    `tenant_id`/`user_id`, all 4 hierarchy ids are deterministically derived
    (UUIDv5)."""

    tenant_id: UUID
    user_id: UUID


def _require_open(
    entity: LegalEntity | Fund | Portfolio | SubAccount | None,
    *,
    kind: str,
    entity_id: UUID,
) -> None:
    if entity is None:
        raise EntityContextResolutionError(
            f"{kind} {entity_id}가 존재하지 않거나(미부트스트랩) 다른 테넌트 소유입니다."
        )
    if entity.closed_at is not None:
        raise EntityContextResolutionError(
            f"{kind} {entity_id}는 폐쇄되어 쓰기 컨텍스트로 쓸 수 없습니다."
        )


async def resolve_context(repo: EntityRepository, request: ResolveContextRequest) -> EntityContext:
    """Single entry point for all order/position/ledger writes. Looks up the
    4-layer hierarchy using deterministic ids (FA-1), and fail-closes (raises
    `EntityContextResolutionError`) if any layer is missing or closed."""
    entity_id = default_entity_id(request.user_id)
    fund_id = default_fund_id(request.user_id)
    portfolio_id = default_portfolio_id(request.user_id)
    sub_account_id = default_sub_account_id(request.user_id)

    entity = await repo.get_legal_entity(request.tenant_id, entity_id)
    _require_open(entity, kind="LegalEntity", entity_id=entity_id)
    fund = await repo.get_fund(request.tenant_id, fund_id)
    _require_open(fund, kind="Fund", entity_id=fund_id)
    portfolio = await repo.get_portfolio(request.tenant_id, portfolio_id)
    _require_open(portfolio, kind="Portfolio", entity_id=portfolio_id)
    sub_account = await repo.get_sub_account(request.tenant_id, sub_account_id)
    _require_open(sub_account, kind="SubAccount", entity_id=sub_account_id)

    return EntityContext(
        tenant_id=request.tenant_id,
        legal_entity_id=entity_id,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        sub_account_id=sub_account_id,
    )


async def verify_entity_context(repo: EntityRepository, ctx: EntityContext) -> None:
    """task-1925 (follow-up to the FA-5 review REJECT) — an `EntityContext`
    passed directly by the caller can be forged (e.g. a fund_id owned by
    another tenant under the same tenant_id). A write entry point (e.g.
    `submit_order`) must re-look-up `ctx`'s four ids by this function, keyed
    on `ctx.tenant_id`, right before INSERT — if any is missing (including
    cross-tenant; FA-2 lookups fold into 404-isomorphic) or closed, the
    value is not trusted and is rejected with `EntityContextResolutionError`
    (fail-closed). Reuses the same `_require_open` composition rule as
    `resolve_context()` rather than inventing a new resolution rule."""
    entity = await repo.get_legal_entity(ctx.tenant_id, ctx.legal_entity_id)
    _require_open(entity, kind="LegalEntity", entity_id=ctx.legal_entity_id)
    fund = await repo.get_fund(ctx.tenant_id, ctx.fund_id)
    _require_open(fund, kind="Fund", entity_id=ctx.fund_id)
    portfolio = await repo.get_portfolio(ctx.tenant_id, ctx.portfolio_id)
    _require_open(portfolio, kind="Portfolio", entity_id=ctx.portfolio_id)
    sub_account = await repo.get_sub_account(ctx.tenant_id, ctx.sub_account_id)
    _require_open(sub_account, kind="SubAccount", entity_id=ctx.sub_account_id)


async def resolve_portfolio_scope(
    repo: EntityRepository, tenant_id: UUID, portfolio_id: UUID
) -> Portfolio:
    """FA-6 — resolution used when a read path (position/performance lookup)
    scopes explicitly by `portfolio_id`. Like `resolve_context()`, it confirms
    ownership/openness across the whole hierarchy (LegalEntity -> Fund ->
    Portfolio) via `_require_open`, but does not enforce `sub_account_id` —
    the read scope is at the portfolio level, and this function does not
    build the write-only 5-field contract `EntityContext` (changing that
    contract is out of scope for this leaf's files). On failure
    (nonexistent/closed/other tenant), it raises
    `EntityContextResolutionError` fail-closed without guessing a value —
    the caller must reject rather than return everything (prevents
    cross-portfolio leakage)."""
    portfolio = await repo.get_portfolio(tenant_id, portfolio_id)
    _require_open(portfolio, kind="Portfolio", entity_id=portfolio_id)
    portfolio = cast(Portfolio, portfolio)

    fund = await repo.get_fund(tenant_id, portfolio.fund_id)
    _require_open(fund, kind="Fund", entity_id=portfolio.fund_id)
    fund = cast(Fund, fund)

    entity = await repo.get_legal_entity(tenant_id, fund.entity_id)
    _require_open(entity, kind="LegalEntity", entity_id=fund.entity_id)

    return portfolio


__all__ = [
    "EntityContextResolutionError",
    "EntityRepository",
    "ResolveContextRequest",
    "resolve_context",
    "resolve_portfolio_scope",
    "verify_entity_context",
]
