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

F1 (task-8808, docs/audits/AUDIT_2026-09-29_ledger_accounting.md): when
`MATERIAL_MISMATCH` is detected, `reconcile_account` attempts one narrow
self-healing resync before returning. The resync re-uses `rebuild_snapshot`
(LB-13) — it re-folds the affected `position_key`s from `pos_journal`, never
overwrites `pos_snapshot` with the provider's balance directly. Writing the
provider value straight into `pos_snapshot` would break the §4.3 invariant
"snapshot = fold(journal)"; a provider/internal divergence that survives a
re-fold means the journal itself is missing an event (a real break), which
this leaf cannot safely fabricate — that case falls back to the existing
manual-intervention path (`MATERIAL_MISMATCH` state, unresolved). Per-position
serialization comes for free from `rebuild_snapshot`'s own `pos_journal`
advisory lock (same namespace as `record_fill`) — this leaf adds no locking
of its own. Resync is opt-in via `journal`/`clock` (both `None` by default) so
existing callers (`scheduler.py`, pre-F1 tests) are unaffected; only errors
raised while building the resync inputs (`journal.list_for`, `provider.balances`
called again) propagate uncaught (DoD #1, "do not swallow exceptions") — a rebuild that
completes but still disagrees with the provider is not an exception, it is the
expected "resync failed, fall back" outcome.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.core.observability.metric_names import (
    POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL,
    POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL,
    POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import AssetClass
from src.foundation.positions.application.rebuild_snapshot import rebuild_snapshot
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.reconciliation_rules import (
    InternalEntityValue,
    build_entity_snapshots,
)
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
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


async def _open_positions_by_asset(
    conn: asyncpg.Connection, snapshots: SnapshotRepository, tenant_id: UUID, account_id: UUID
) -> tuple[dict[str, Decimal], dict[str, list[str]]]:
    """Return (asset -> summed quantity, asset -> its `position_key`s)."""
    open_positions = await snapshots.list_open(conn, tenant_id, account_id)
    by_quantity: dict[str, Decimal] = {}
    by_keys: dict[str, list[str]] = {}
    for snapshot in open_positions:
        asset = PositionKey.parse(snapshot.position_key).instrument_id
        by_quantity[asset] = by_quantity.get(asset, Decimal(0)) + snapshot.quantity
        by_keys.setdefault(asset, []).append(snapshot.position_key)
    return by_quantity, by_keys


async def _resync_mismatched_positions(
    mismatched_assets: set[str],
    *,
    position_keys_by_asset: dict[str, list[str]],
    tenant_id: UUID,
    account_id: UUID,
    connection_id: UUID,
    snapshots: SnapshotRepository,
    provider: ProviderBalanceSource,
    recon: RunReconciliation,
    pool: asyncpg.Pool,
    journal: PositionJournalRepository,
    clock: Callable[[], datetime],
    asset_class: AssetClass,
) -> ReconciliationRunView:
    """Re-fold the journal for every `position_key` behind a mismatched asset
    (LB-13 `rebuild_snapshot`, which already holds the `pos_journal` advisory
    lock per `position_key`), then re-run FND-08 reconciliation once to see
    whether the re-fold actually closed the gap against the provider."""
    for asset in mismatched_assets:
        for position_key in position_keys_by_asset.get(asset, ()):
            await rebuild_snapshot(
                position_key,
                tenant_id=tenant_id,
                asset_class=asset_class,
                journal=journal,
                snapshots=snapshots,
                pool=pool,
                clock=clock,
                dry_run=False,
            )

    balances = await provider.balances(connection_id)
    provider_map: dict[str, Decimal] = {b.asset: b.total for b in balances}
    async with pool.acquire() as conn:
        internal_by_asset, _ = await _open_positions_by_asset(
            conn, snapshots, tenant_id, account_id
        )
    internal = [
        InternalEntityValue(entity_type=_ENTITY_TYPE, entity_key=asset, value=quantity)
        for asset, quantity in internal_by_asset.items()
    ]
    entities = build_entity_snapshots(internal, provider_map)
    return await recon(
        tenant_id=tenant_id,
        target_type=_TARGET_TYPE,
        target_ref=account_id,
        connection_id=None,
        entities=entities,
    )


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
    journal: PositionJournalRepository | None = None,
    clock: Callable[[], datetime] | None = None,
    asset_class: AssetClass = AssetClass.CRYPTO,
) -> ReconciliationRunView:
    balances = await provider.balances(connection_id)
    provider_map: dict[str, Decimal] = {b.asset: b.total for b in balances}

    async with pool.acquire() as conn:
        internal_by_asset, position_keys_by_asset = await _open_positions_by_asset(
            conn, snapshots, tenant_id, account_id
        )

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

    mismatched_assets = {
        item.entity_key
        for item in result.items
        if item.classification == Classification.MATERIAL_MISMATCH
    }
    material_count = len(mismatched_assets)
    if material_count:
        registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).inc(material_count)

        if journal is not None and clock is not None:
            resynced = await _resync_mismatched_positions(
                mismatched_assets,
                position_keys_by_asset=position_keys_by_asset,
                tenant_id=tenant_id,
                account_id=account_id,
                connection_id=connection_id,
                snapshots=snapshots,
                provider=provider,
                recon=recon,
                pool=pool,
                journal=journal,
                clock=clock,
                asset_class=asset_class,
            )
            still_mismatched = any(
                item.classification == Classification.MATERIAL_MISMATCH
                for item in resynced.items
                if item.entity_key in mismatched_assets
            )
            if still_mismatched:
                registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).inc()
            else:
                registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).inc()
                result = resynced

    return result
