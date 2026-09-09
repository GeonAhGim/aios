"""H-1a — mandate binding resolver.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-1
(split from task-2603, task-3368). The order assembly layer
(order_service/foundation_gate.py, submit_order.py) needs somewhere to ask
"which mandate revision can this order's portfolio currently reference"
before it can switch to `require_mandate=True` — this module does only that
one lookup. Assembly wiring (H-1 proper) and caching (H-11: immediate
invalidation on state-change events) are both out of scope for this leaf —
it reads the DB directly on every call.

Takes only `portfolio_id`, not `tenant_id`: the only numbering path today,
FA-1's `default_portfolio_id(user_id)` in
`src/foundation/entities/domain/defaults.py`, deterministically derives a
per-user value from a fixed-namespace UUIDv5, so even under the current
schema (`portfolio_mandate` `UNIQUE(tenant_id, portfolio_id)`) the owning
tenant is already effectively pinned down by `portfolio_id` alone.
**Unverified**: FA-0b's schema does allow a tenant to be explicitly issued
multiple portfolio_ids — if that path ever comes into use this assumption
breaks, and this function will need to take `tenant_id` too.

The three values of `MandateRevisionRef.status` carry over the same
three-way classification `application/evaluate_policy.py` already branches
on (PAUSED handled separately as `PAUSE_REQUIRED`, every other non-ACTIVE
state lumped into `STATE_NO_ACTIVE_MANDATE`) — `SUPERSEDED`/`CANCELLED`/
`DRAFT`/`PROPOSED` all mean "this pointer is no longer a valid mandate", the
same conclusion, so they collapse into one `EXPIRED` (the exact prior state
stays available on `MandateRevisionRef.revision.state` for callers that
want finer granularity). If the `portfolio_mandate` row itself doesn't
exist, or `active_revision_id` is NULL (including a mandate where no
revision has ever been activated), it returns `None` — "absent" isn't a
branch of the result type but true absence, hence `Optional`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

import asyncpg

from src.foundation.mandates.domain.models import Autonomy, MandateRevision, MandateRevisionState


class MandateBindingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class MandateRevisionRef:
    status: MandateBindingStatus
    revision: MandateRevision


def _row_to_revision(row: asyncpg.Record) -> MandateRevision:
    return MandateRevision(
        id=row["id"],
        mandate_id=row["mandate_id"],
        revision_no=row["revision_no"],
        state=MandateRevisionState(row["state"]),
        max_total_exposure_pct=float(row["max_total_exposure_pct"]),
        max_single_instrument_pct=float(row["max_single_instrument_pct"]),
        min_cash_buffer_pct=float(row["min_cash_buffer_pct"]),
        max_daily_loss_pct=float(row["max_daily_loss_pct"]),
        allowed_autonomy=Autonomy(row["allowed_autonomy"]),
        forbidden_assets=tuple(row["forbidden_assets"]),
        revision_hash=row["revision_hash"],
        cooling_off_started_at=row["cooling_off_started_at"],
        created_at=row["created_at"],
        activated_at=row["activated_at"],
    )


def _status_for_state(state: MandateRevisionState) -> MandateBindingStatus:
    if state == MandateRevisionState.ACTIVE:
        return MandateBindingStatus.ACTIVE
    if state == MandateRevisionState.PAUSED:
        return MandateBindingStatus.PAUSED
    return MandateBindingStatus.EXPIRED


async def resolve_mandate_revision(
    pool: asyncpg.Pool, portfolio_id: UUID
) -> MandateRevisionRef | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT r.* FROM portfolio_mandate m "
            "JOIN mandate_revision r ON r.id = m.active_revision_id "
            "WHERE m.portfolio_id = $1",
            portfolio_id,
        )
    if row is None:
        return None
    revision = _row_to_revision(row)
    return MandateRevisionRef(status=_status_for_state(revision.state), revision=revision)
