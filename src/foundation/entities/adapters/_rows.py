"""FA-2 -- asyncpg.Record -> 계약 dataclass 변환 함수 4종.

postgres_repository.py가 300줄 상한(P6)을 넘겨 엔티티 레벨별 믹스인으로
쪼갠 것 중 공용 부분 -- 각 믹스인이 자기 레벨 것뿐 아니라 자식 레벨
변환기도 필요(list_*_by_* 반환 타입)해 한 곳에 모았다.
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
