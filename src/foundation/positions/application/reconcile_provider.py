"""LB-16 — Exchange balance reconciliation (application/reconcile_provider.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-16.

Assembles internal open positions (quantity sums) and provider (exchange)
balances into an `EntitySnapshot` list via LB-6
`reconciliation_rules.build_entity_snapshots`; classification and aggregation
are fully delegated to FND-08 `run_reconciliation` (injected `recon`) —
tolerance and verdict grades are not reimplemented here (task-726 decision).
Reconciliation is read-only: this function does not update `pos_snapshot`/journals,
writing only to `reconciliation_run/item/state` tables already owned by FND-08
(no new tables, task-726 decision).

`entity_key` approximates `PositionKey.instrument_id` as an asset code to match
provider `AccountBalance.asset` — there is no guarantee it exactly matches the
actual exchange balance asset code (unverified). Exact mapping for derivatives
and composite symbols is left to a follow-up leaf task.

`connection_id` in the `recon` call is always fixed to `None`: if FND-08
`run_reconciliation` receives a non-`connection_id`, it first checks the health
status of FND-05 `account_connection` and overwrites the entire result with
`PROVIDER_UNAVAILABLE` before individual verdicts if unhealthy (80th §2). The
`connection_id` received by this leaf is the key `provider.balances()` uses to
look up the adapter and does not necessarily point to an `account_connection`
row; passing it as-is would reference a non-existent FK (reconciliation save
failure) or trigger an unintended health gate — FND-05 integration is out of
scope for this leaf, so we bypass it.

Exceptions raised by `provider.balances()` are not swallowed but propagated as-is
(fail-closed, DoD #3) — calls are per-account, so a failure for one account does
not affect calls for other accounts via shared state.
"""
from __future__ import annotations

from collections.abc import Awaitable
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.core.observability.metric_names import POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.reconciliation_rules import (
    InternalEntityValue,
    build_entity_snapshots,
)
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationRunView,
)

__all__ = ["RunReconciliation", "reconcile_account"]

_TARGET_TYPE = "POSITIONS_EXCHANGE_BALANCE"
_ENTITY_TYPE = "EXCHANGE_BALANCE"


class RunReconciliation(Protocol):
    """Call contract for FND-08 `run_reconciliation` (with 3 repositories already
    bound) — this leaf knows only this Protocol, not the repositories (71st §4)."""

    def __call__(
        self,
        *,
        tenant_id: UUID,
        target_type: str,
        target_ref: UUID,
        connection_id: UUID | None,
        entities: list[EntitySnapshot],
    ) -> Awaitable[ReconciliationRunView]: ...


async def reconcile_account(
    tenant_id: UUID,
    account_id: UUID,
    *,
    connection_id: UUID,
    snapshots: SnapshotRepository,
    provider: ProviderBalanceSource,
    recon: RunReconciliation,
    pool: asyncpg.Pool,
    registry: MetricsRegistry,
) -> ReconciliationRunView:
    balances = await provider.balances(connection_id)
    provider_map: dict[str, Decimal] = {b.asset: b.total for b in balances}

    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, tenant_id, account_id)

    internal_by_asset: dict[str, Decimal] = {}
    for snapshot in open_positions:
        asset = PositionKey.parse(snapshot.position_key).instrument_id
        internal_by_asset[asset] = internal_by_asset.get(asset, Decimal(0)) + snapshot.quantity

    internal = [
        InternalEntityValue(entity_type=_ENTITY_TYPE, entity_key=asset, value=quantity)
        for asset, quantity in internal_by_asset.items()
    ]
    entities = build_entity_snapshots(internal, provider_map)

    result = await recon(
        tenant_id=tenant_id,
        target_type=_TARGET_TYPE,
        target_ref=account_id,
        connection_id=None,
        entities=entities,
    )

    material_count = sum(
        1 for item in result.items if item.classification == Classification.MATERIAL_MISMATCH
    )
    if material_count:
        registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).inc(material_count)

    return result
