"""DC-27 — source_contract storage port.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. A future paid ingest_source adapter (OPENDART/ECOS/KRX/EODHD, etc., D6
"not registered before a contract exists") only knows the `source_id`
string and knows nothing about this port — this port is the storage
contract the `application/authorize_source_access.py` gate reads before a
call.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import SourceContract

__all__ = ["SourceContractRepository"]


@runtime_checkable
class SourceContractRepository(Protocol):
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        """The current contract row for `source_id`. A tier upgrade is an
        UPDATE to this row, not inserting a new row (D1) — returns `None`
        if the row is absent and lets the caller reject fail-closed."""
        ...
