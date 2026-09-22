"""FA-1 — Automatic creation rules for individual user default entity hierarchy (pure).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (§2.1·§9 FA-1 DoD).

DoD: "No change to existing single-account UX". Until now, users have traded
with a single account without legal entity/fund/portfolio concepts — when
FA-3~6 begin enforcing `fund_id`/`portfolio_id` on all order/position/ledger
writes, each existing user must automatically have one set of
"personal default legal entity/fund/portfolio/sub-account" so that the
enforcement does not break UX.

**Why ID generation is defined as deterministic (UUIDv5, user_id-based)**: This
hierarchy must be reproducible across multiple locations (router validation,
cache keys, tests) with the same id even before FA-2 (persistence). If a new
random UUID is issued each time, you cannot answer "what is this user's
default portfolio" without a lookup, and FA-3 retroactive
backfill (filling fund_id/portfolio_id on existing orders/fills) would also
rely on a stored mapping table. UUIDv5 always produces the same id from a
(fixed namespace, namespace string, user_id) tuple — so even if the migration
script and runtime code each compute it without a lookup, they always agree
(idempotent). The namespace constant, once fixed, never changes (if it did,
the default hierarchy ids for all existing users would change and FA-3
backfill would break).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount

# Fixed namespace. Hardcoded result of
# `uuid.uuid5(uuid.NAMESPACE_URL,
# "https://aios.internal/foundation/entities/default-hierarchy/v1")` —
# changing this constant changes the default hierarchy ids for all existing
# users (never modify; if a change is needed, add a new constant + migration).
_DEFAULT_HIERARCHY_NAMESPACE = UUID("5c81612c-72bf-5cda-8e53-109f3d102e52")

_DEFAULT_ENTITY_NAME = "Personal Account"


def default_entity_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"legal_entity:{user_id}")


def default_fund_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"fund:{user_id}")


def default_portfolio_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"portfolio:{user_id}")


def default_sub_account_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"sub_account:{user_id}")


@dataclass(frozen=True)
class DefaultHierarchy:
    legal_entity: LegalEntity
    fund: Fund
    portfolio: Portfolio
    sub_account: SubAccount


def build_default_hierarchy(
    *,
    user_id: UUID,
    tenant_id: UUID,
    base_currency: Currency,
    jurisdiction: str,
    region_tag: str,
    venue_account_ref: str,
    inception: date,
    name: str = _DEFAULT_ENTITY_NAME,
) -> DefaultHierarchy:
    """Deterministically assemble the 4-tier hierarchy for a single user. This
    function is pure — it does not check persistence or existence (idempotent
    upsert is the FA-2 adapter's responsibility).
    `jurisdiction`/`region_tag`/`base_currency`/`venue_account_ref` are values
    from the user profile/exchange connection info, so no defaults are assumed
    here — what needs to be deterministic is the id only."""
    entity = LegalEntity(
        entity_id=default_entity_id(user_id),
        tenant_id=tenant_id,
        name=name,
        jurisdiction=jurisdiction,
        region_tag=region_tag,
    )
    fund = Fund(
        fund_id=default_fund_id(user_id),
        entity_id=entity.entity_id,
        base_currency=base_currency,
        inception=inception,
    )
    portfolio = Portfolio(
        portfolio_id=default_portfolio_id(user_id),
        fund_id=fund.fund_id,
        venue_account_ref=venue_account_ref,
    )
    sub_account = SubAccount(
        sub_account_id=default_sub_account_id(user_id),
        portfolio_id=portfolio.portfolio_id,
        owner_ref=user_id,
    )
    return DefaultHierarchy(
        legal_entity=entity, fund=fund, portfolio=portfolio, sub_account=sub_account
    )
