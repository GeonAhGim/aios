"""DC-27 — asyncpg implementation of `ports/source_contract_repository.py`.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. Only reads the `source_contract` table (migration created in the same
leaf as this file) — tier upgrade (row UPDATE) belongs to ops tooling/admin
routers, and this adapter does not yet expose writes (for the same reason as
D6's "don't pre-register adapters before a contract exists": the write path
is added in a later leaf once the actual contract-registration operational
procedure is decided).
"""
from __future__ import annotations

import json

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)

__all__ = ["PostgresSourceContractRepository"]


def _row_to_contract(row: asyncpg.Record) -> SourceContract:
    capability_raw = row["capability"]
    capability_dict = (
        json.loads(capability_raw) if isinstance(capability_raw, str) else capability_raw
    )
    return SourceContract(
        source_id=row["source_id"],
        tier=SourceContractTier(row["tier"]),
        credential_ref=row["credential_ref"],
        redistribution_scope=RedistributionScope(row["redistribution_scope"]),
        rate_limit=row["rate_limit"],
        quota=row["quota"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        capability=SourceCapability(
            asset_classes=frozenset(capability_dict["asset_classes"]),
            resolutions=frozenset(capability_dict["resolutions"]),
            corporate_actions=capability_dict["corporate_actions"],
        ),
    )


class PostgresSourceContractRepository:
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        row = await conn.fetchrow(
            "SELECT * FROM source_contract WHERE source_id = $1", source_id
        )
        if row is None:
            return None
        return _row_to_contract(row)
