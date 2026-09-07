"""DC-27 — gate in front of adapters that know only source_id.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. The sole point a future paid ingest_source adapter passes through before
a call — the adapter function signature takes only the `source_id` string,
and this gate reads the `source_contract` row to determine tier,
capability, and redistribution scope. Since a tier upgrade is only an
UPDATE to that row, neither this function nor adapter code changes
(task-1764 DoD).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import (
    SourceContractGrant,
    authorize_source,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository

__all__ = ["authorize_source_access"]


async def authorize_source_access(
    conn: asyncpg.Connection,
    source_id: str,
    *,
    repo: SourceContractRepository,
    clock: Callable[[], datetime],
) -> SourceContractGrant:
    """Called before the adapter actually fetches `source_id` data. If the
    returned `SourceContractGrant.allowed` is `False`, the adapter must not
    be called — the determination ends here, and the adapter is never
    handed a tier at all."""
    contract = await repo.get(conn, source_id)
    return authorize_source(contract, clock())
