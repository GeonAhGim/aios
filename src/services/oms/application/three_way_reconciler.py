"""L4-24 — 3-way reconciliation (internal orders vs exchange query) orchestration + I12 wiring.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `three_way_reconciler.py`,
§9 L4-24, §4.1 I11/I12, §6 F7.

Scope reduction (documented, following the same precedent as the
`foundation/reconciliation/application/run_reconciliation.py` docstring): this
codebase does not yet have an internal balance/position ledger that OMS can
reference ("paper_control (FND-07) does not yet have an internal
fill/position/balance ledger") — so this leaf compares **orders only** (local
open orders vs `ExchangeAdapter.get_open_orders()`). The `get_order_history`/
`get_fills`/`get_balance` dependencies listed in the spec's §2-C table have
nothing to compare against until the internal ledger exists, so they are not
called — once the ledger exists, adding BALANCE_MISMATCH/FILL_MISSING_INTERNAL
passes to `_compare` is all that's needed (`reconcile_rules.compare_triple`
already accepts that parameter).

I12 ("while MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE is aggregated, deny new
SUBMITs until the ACCOUNT-scope safety control is ACTIVE") is enforced here by
calling `activate_safety_control`/`deactivate_safety_control` directly
(scope=ACCOUNT, scope_ref=str(tenant_id)). `submit_order`'s
(`order_service/foundation_gate.py`) `pre_submit_gate` already reads all five
pairs from `fence_pairs_for`, including the ACCOUNT pair, on every submission
and DENYs if any control is ACTIVE (I-01, reused unchanged) — the wiring this
file does is only to create and remove the control rows that existing gate
reads. Removing this file's `activate_safety_control` call would fail the
DENY assertion in `test_three_way_reconciler.py` (proof of wiring).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.trading import Order as ProviderOrder
from src.data.models.trading import OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.reconciliation.contracts.v1 import Classification
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.order_query import list_orders
from src.services.oms.contracts.v1_events import Discrepancy
from src.services.oms.contracts.v1_views import OrderView, ReconcileSummaryView
from src.services.oms.domain.reconcile_rules import compare_triple

logger = logging.getLogger(__name__)

DEFAULT_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.01"), relative_tolerance_pct=Decimal("0.1")
)
_OPEN_STATUSES = (OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED)
_BLOCKING = frozenset({Classification.MATERIAL_MISMATCH, Classification.PROVIDER_UNAVAILABLE})
_BLOCK_REASON_PREFIX = "INTEGRITY_RECONCILIATION_MISMATCH"
# Same ordering as ticket #80 §2 "typed severity". Since `compare_triple`
# never includes HEALTHY entries in its result (§9 DoD "the normal case
# reports 0 items"), an empty list is judged HEALTHY directly by the caller,
# not by `_aggregate` (an empty tuple -> PENDING in
# `foundation.reconciliation.domain.rules.aggregate_classification` means
# "never reconciled", which differs from this context).
_SEVERITY = (
    Classification.MATERIAL_MISMATCH,
    Classification.PROVIDER_UNAVAILABLE,
    Classification.MINOR_DIFFERENCE,
    Classification.HEALTHY,
)


class ProviderUnavailableError(Exception):
    """Internal signal for `adapter.get_open_orders()` failure (timeout/network/
    unauthenticated) — absorbed as REC-003 `PROVIDER_UNAVAILABLE` instead of
    propagating outside this module."""


def _aggregate(materialities: list[Classification]) -> Classification:
    if not materialities:
        return Classification.HEALTHY
    present = set(materialities)
    for candidate in _SEVERITY:
        if candidate in present:
            return candidate
    return Classification.HEALTHY


def _provider_order_view(internal: OrderView, provider: ProviderOrder) -> OrderView:
    """Reuses the same `order_id` as `internal` so `compare_triple`'s dict join
    matches it as one order (the actual matching key is `client_order_id`,
    already confirmed by the caller — `OrderView.order_id` is a key that is
    only meaningful inside OMS, so the provider side has no corresponding
    value)."""
    return internal.model_copy(
        update={
            "status": provider.status,
            "filled_quantity": provider.filled_quantity,
            "average_fill_price": (
                provider.average_fill_price.amount
                if provider.average_fill_price is not None
                else None
            ),
        }
    )


async def _fetch_provider_orders(adapter: ExchangeAdapter) -> list[ProviderOrder]:
    try:
        return await adapter.get_open_orders()
    except Exception as exc:  # noqa: BLE001 — I11: adapter failure is PROVIDER_UNAVAILABLE, not an assumed 0
        raise ProviderUnavailableError(str(exc)) from exc


async def _connection_unavailable(pool: asyncpg.Pool, connection_id: UUID | None) -> bool:
    if connection_id is None:
        return False
    health = await PostgresConnectionRepository(pool).get_latest_health(connection_id)
    return health is None or health.state.value != "HEALTHY"


async def _apply_account_gate(
    risk_repo: RiskGateRepository, *, tenant_id: UUID, blocked: bool, reason: str
) -> None:
    """I12 — keeps the ACCOUNT-scope safety control ACTIVE while
    MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE is open, and deactivates it as soon
    as it's resolved.
    REC-006 rerun dedupe — `activate_safety_control` (risk_gate) does not
    populate its own idempotency_digest (see `insert_safety_control`), so every
    call creates a new row. This function is responsible for not creating a
    duplicate when an ACTIVE ACCOUNT control with the same `reason` already
    exists."""
    scope_ref = str(tenant_id)
    existing = [
        c
        for c in await risk_repo.list_active_controls(tenant_id=tenant_id)
        if c.scope == SafetyScope.ACCOUNT and c.scope_ref == scope_ref
    ]

    if blocked:
        if any(c.reason == reason for c in existing):
            return
        # This is the reconciliation engine's own judgment, not a human's (same rationale
        # as run_reconciliation.py).
        await activate_safety_control(
            risk_repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            scope=SafetyScope.ACCOUNT,
            scope_ref=scope_ref,
            reason=reason,
        )
        return

    for control in existing:
        if control.reason.startswith(_BLOCK_REASON_PREFIX):
            await deactivate_safety_control(
                risk_repo, tenant_id=tenant_id, actor_is_admin=True, control_id=control.id
            )


async def reconcile_account(
    *,
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    tenant_id: UUID,
    connection_id: UUID | None,
    account_ref: str,
    window: timedelta,
    policy: MaterialityPolicy = DEFAULT_POLICY,
) -> ReconcileSummaryView:
    now = datetime.now(timezone.utc)
    window_start = now - window
    risk_repo = PostgresRiskGateRepository(pool)

    internal_orders, _ = await list_orders(pool, tenant_id=tenant_id, statuses=_OPEN_STATUSES)

    provider_unavailable = await _connection_unavailable(pool, connection_id)
    discrepancies: list[Discrepancy] = []
    if not provider_unavailable:
        try:
            provider_orders_raw = await _fetch_provider_orders(adapter)
        except ProviderUnavailableError:
            provider_unavailable = True
        else:
            by_client_id = {o.client_order_id: o for o in provider_orders_raw}
            provider_views = [
                _provider_order_view(internal, by_client_id[internal.client_order_id])
                for internal in internal_orders
                if internal.client_order_id in by_client_id
            ]
            discrepancies = compare_triple(internal_orders, provider_views, [], {}, {}, policy)

    if provider_unavailable:
        targets: list[OrderView | None] = list(internal_orders) or [None]
        discrepancies = [
            Discrepancy(
                kind="FILLED_QTY_MISMATCH",
                entity_key=str(order.order_id) if order is not None else account_ref,
                internal_value=order.filled_quantity if order is not None else None,
                provider_value=None,
                materiality=Classification.PROVIDER_UNAVAILABLE,
            )
            for order in targets
        ]
        overall = Classification.PROVIDER_UNAVAILABLE
    elif not discrepancies:
        overall = Classification.HEALTHY
    else:
        overall = _aggregate([d.materiality for d in discrepancies])

    await _apply_account_gate(
        risk_repo,
        tenant_id=tenant_id,
        blocked=overall in _BLOCKING,
        reason=f"{_BLOCK_REASON_PREFIX}:{overall.value}",
    )

    return ReconcileSummaryView(
        account_ref=account_ref,
        window_start=window_start,
        window_end=now,
        discrepancies=discrepancies,
        overall_classification=overall.value,
        checked_at=now,
    )
