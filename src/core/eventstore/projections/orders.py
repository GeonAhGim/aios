"""FA-14 — orders projection: replay `order_events`(OMS) + `fills` into order state.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4, §9 FA-14
("define a projection using the existing order_events(OMS) as the event
source (no new event tables)").

Pure fold, no I/O: `project()` takes the `order_events` timeline for one
order (`OrderTransitionEvent`, L4-06/07 contract, reused as-is) plus the
`fills` timeline for the same order (`FillEvent`, L4-08 contract) and folds
both into `OrderProjection`. Loading either timeline from Postgres is the
adapter's job (`order_events_repository.timeline()` / `fills_repository.
list_for_order()`) and stays out of this module.

Two sources, not one (task-2050 decision, adopted after the single-source
attempt found that `order_events` rows carry only `from_status`/`to_status`/
`payload_hash` — no payload, so `filled_quantity`/`average_fill_price`/
`fee_total`/`fee_currency` can never be replayed from `order_events` alone):

- `status`/`version`/`updated_at` come from `order_events` (unchanged).
- `filled_quantity`/`average_fill_price`/`fee_total`/`fee_currency` come from
  folding `fills` through `fill_normalizer.aggregate()`(L4-05) — the exact
  function `inbox_processor._process_row` already uses to compute those same
  `orders` columns at write time (reused, not a new aggregation rule).

Version-reconciliation subtlety this fold accounts for: a `FILL`-type
`order_events` row is always preceded by one *silent* `orders.version` bump
that has no event of its own — `FillsRepository.insert_if_absent()` updates
`orders.filled_quantity` before `order_repository.transition()` writes the
paired event, and `oms_enforce_order_transition_trg` bumps `version` on
every `UPDATE orders` unconditionally (073beca589d5 `_GUARD_FN_SQL`), not
only on the ones that carry an event. So one `FILL` event corresponds to
`version += 2`; every other event to `version += 1`. Folding every event as
`+= 1` (as if `order_events` rows and `orders.version` moved 1:1) would make
`OrderProjection.version` fall behind `orders.version` by one for every fill
once any fill has been recorded — exactly the kind of discrepancy this
leaf's DoD(1) full-field-parity test is meant to catch.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.data.models.trading import OrderStatus
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent
from src.services.oms.domain.fill_normalizer import aggregate
from src.services.oms.domain.state_machine import OrderEvent

_INITIAL_STATUS = OrderStatus.CREATED


class EventChainBrokenError(Exception):
    """`ES_ORDER_CHAIN_BROKEN` — an event's `from_status` does not match the
    status already folded so far (a missing or reordered event). Replay is
    fail-closed: this is the defect FA-14 DoD(2) requires a negative test to
    actually trigger, not a decorative check."""

    def __init__(self, order_id: UUID, expected: OrderStatus, event: OrderTransitionEvent) -> None:
        super().__init__(
            f"order_id={order_id}: seq={event.seq} from_status={event.from_status.value} "
            f"!= folded status so far ({expected.value}) — missing or reordered event."
        )
        self.order_id = order_id
        self.expected = expected
        self.event = event


@dataclass(frozen=True, slots=True)
class OrderProjection:
    """The event-sourceable subset of `orders` (see module docstring)."""

    order_id: UUID
    status: OrderStatus = _INITIAL_STATUS
    version: int = 0
    last_seq: int = 0
    updated_at: datetime | None = None
    # Folded by project() over the complete `fills` sequence, not by
    # apply_one() — None until project() runs (see module docstring).
    filled_quantity: Decimal | None = None
    average_fill_price: Decimal | None = None
    fee_total: Decimal | None = None
    fee_currency: str | None = None


def apply_one(state: OrderProjection, event: OrderTransitionEvent) -> OrderProjection:
    """Fold one `order_events` row into `state` (`status`/`version`/
    `updated_at` only — fill-derived fields are folded separately by
    `project()`, once, over the complete `fills` sequence)."""
    if event.from_status is not state.status:
        raise EventChainBrokenError(state.order_id, state.status, event)
    version_step = 2 if event.event == OrderEvent.FILL.value else 1
    return replace(
        state,
        status=event.to_status,
        version=state.version + version_step,
        last_seq=event.seq if event.seq is not None else state.last_seq,
        updated_at=event.occurred_at,
    )


def project(
    order_id: UUID,
    events: Sequence[OrderTransitionEvent],
    fills: Sequence[FillEvent] = (),
) -> OrderProjection:
    """Fold `events`(seq ascending, `order_events.timeline()` order) from the
    start, then fold the complete `fills`(`fills_repository.list_for_order()`
    order) sequence via `fill_normalizer.aggregate()`. `orders` rows always
    start at `CREATED`(DB `DEFAULT`) with no event of their own, so that is
    the events fold's initial state."""
    state = OrderProjection(order_id=order_id)
    for event in events:
        state = apply_one(state, event)

    agg = aggregate(fills)
    fee_currency, fee_total = (
        next(iter(agg.fee_total.items())) if agg.fee_total else (None, None)
    )
    return replace(
        state,
        filled_quantity=agg.filled_qty,
        average_fill_price=agg.avg_price if fills else None,
        fee_total=fee_total,
        fee_currency=fee_currency,
    )
