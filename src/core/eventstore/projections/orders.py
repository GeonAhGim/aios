"""FA-14 — orders projection: replay `order_events`(OMS) into order state.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4, §9 FA-14
("기존 order_events(OMS)를 이벤트 원천으로 삼아 투영 정의(새 이벤트 테이블
신설 금지)").

Pure fold, no I/O: `project()` takes the `order_events` timeline for one
order (already exposed as `OrderTransitionEvent`, L4-06/07 contract — reused
as-is, not re-defined) and folds it into `OrderProjection`. Loading the
timeline from Postgres is the adapter's job (`order_events_repository.
timeline()`, L4-07) and stays out of this module.

KNOWN GAP (this is the finding FA-14 was asked to surface, not a bug to
paper over — see task-2050 decision): `order_events` rows carry only
`from_status`/`to_status`/`payload_hash` (073beca589d5 DDL) — the actual
event payload is never persisted, only its hash. `orders.filled_quantity`/
`average_fill_price`/`fee_total`/`fee_currency` are written by
`order_repository.transition()`'s `patch` dict in the same UPDATE as the
status change (`src/services/oms/adapters/order_repository.py`), but that
patch is never event-sourced anywhere. `OrderProjection` therefore only
covers the subset of `orders` that `order_events` can actually reconstruct
(`status`, `version`, `updated_at`) — the same "fold-able subset only"
precedent as `positions.domain.snapshot_builder.SnapshotFold`(LB-5).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.data.models.trading import OrderStatus
from src.services.oms.contracts.v1_events import OrderTransitionEvent

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


def apply_one(state: OrderProjection, event: OrderTransitionEvent) -> OrderProjection:
    """Fold one `order_events` row into `state`."""
    if event.from_status is not state.status:
        raise EventChainBrokenError(state.order_id, state.status, event)
    return OrderProjection(
        order_id=state.order_id,
        status=event.to_status,
        version=state.version + 1,
        last_seq=event.seq if event.seq is not None else state.last_seq,
        updated_at=event.occurred_at,
    )


def project(order_id: UUID, events: Sequence[OrderTransitionEvent]) -> OrderProjection:
    """Fold `events`(seq ascending, `order_events.timeline()` order) from the
    start. `orders` rows always start at `CREATED`(DB `DEFAULT`) with no
    event of their own, so that is the fold's initial state."""
    state = OrderProjection(order_id=order_id)
    for event in events:
        state = apply_one(state, event)
    return state
