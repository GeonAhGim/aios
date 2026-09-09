"""FA-2 -- four asyncpg.Record -> contract dataclass conversion functions.

The shared part after postgres_repository.py exceeded the 300-line cap (P6)
and was split into per-entity-level mixins -- each mixin needs not only its
own level's converter but also its child level's (for list_*_by_* return
types), so they were collected in one place.
"""
from __future__ import annotations

import asyncpg

from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount


def row_to_legal_entity(row: asyncpg.Record) -> LegalEntity:
    return LegalEntity(
        entity_id=row["entity_id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        jurisdiction=row["jurisdiction"],
        region_tag=row["region_tag"],
        closed_at=row["closed_at"],
    )


def row_to_fund(row: asyncpg.Record) -> Fund:
    return Fund(
        fund_id=row["fund_id"],
        entity_id=row["entity_id"],
        base_currency=row["base_currency"],
        mandate_ref=row["mandate_ref"],
        inception=row["inception"],
        closed_at=row["closed_at"],
    )


def row_to_portfolio(row: asyncpg.Record) -> Portfolio:
    return Portfolio(
        portfolio_id=row["portfolio_id"],
        fund_id=row["fund_id"],
        venue_account_ref=row["venue_account_ref"],
        closed_at=row["closed_at"],
    )


def row_to_sub_account(row: asyncpg.Record) -> SubAccount:
    return SubAccount(
        sub_account_id=row["sub_account_id"],
        portfolio_id=row["portfolio_id"],
        owner_ref=row["owner_ref"],
        closed_at=row["closed_at"],
    )
