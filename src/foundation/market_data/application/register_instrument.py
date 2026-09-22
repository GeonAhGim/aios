"""LA-14 — Instrument registration and lifecycle transition use case + 1:1 audit event.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.2, §9.2 LA-14.

Both functions open a connection and transaction from their own `pool` (scaffold signature
`register_instrument(cmd, *, refs, audit, pool)`), calling domain writes and
`AuditEventRepository.append_event_in`(L0-4) together inside — if anything fails and the
transaction rolls back, the audit event disappears with it (§9 LA-14 DoD "1:1 audit event").

Symbol normalization (`to_canonical`) and the lifecycle state machine (`transition`) are
already pure functions provided by LA-7 — this file only attaches audit events to their
decisions and layers guards (duplicate registration, whether the RENAME target symbol is
in use), without reimplementing the rules themselves.

`ReferenceRepository`(LA-9 port) has no single lookup by `instrument_id` (only
`get_instrument` which takes a venue+canonical_symbol+timestamp combination, LA-12
decision — this leaf does not create a new port). So `apply_lifecycle_event`
takes the current state (`current: InstrumentRef`) from the caller — the same reason
`record_fill` takes `asset_class` as a keyword argument (values with nowhere to store /
no port to query yet are passed by the caller).

§4.2 "DELISTED | * | — | Reject | — | outcome=DENIED" row: when `lifecycle.transition`
raises `LifecycleTransitionError` (no transition out of DELISTED in the table), skip the
domain write, commit only the `outcome=DENIED` audit event, and re-raise the exception —
because this function opens the transaction directly (it does not take a caller `conn`
like `post_entry`), to preserve the DENIED audit the exception must be raised **outside**
the `async with` block (if raised inside, the DENIED row just committed is rolled back too).

§4.2 DELIST guard ("open positions == 0 or force flag") requires a B(positions) context
query — LA-14 depends only on LA-12/L0-4 and not on positions (task-616 depends_on), so
this guard is out of scope for this leaf (**unverified**, must be added in a subsequent
leaf after receiving a positions port injection).
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.api import (
    AuditEvent,
    Classification,
    Outcome,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.market_data.contracts.v1 import (
    InstrumentRef,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
)
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleTransitionError,
    transition,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AuditAppender",
    "RenameSymbolInUseError",
    "apply_lifecycle_event",
    "register_instrument",
]

_ACTIONS: dict[str, str] = {
    "LIST": "instrument.listed",
    "SUSPEND": "instrument.suspended",
    "RESUME": "instrument.resumed",
    "DELIST": "instrument.delisted",
    "RENAME": "instrument.renamed",
}


class RenameSymbolInUseError(Exception):
    """§4.2 RENAME guard violation — new_venue_symbol is currently in use."""


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


async def register_instrument(
    pool: asyncpg.Pool,
    cmd: RegisterInstrumentCommand,
    *,
    refs: ReferenceRepository,
    audit: AuditAppender,
) -> InstrumentRef:
    """Register a new instrument. Duplicate (venue, venue_symbol) is immediately rejected
    by `refs.register` with `DuplicateInstrumentError` — no writes occur at all, so no
    audit event is emitted (even a DELISTED symbol with the same venue_symbol is rejected
    through this path)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument = await refs.register(conn, cmd)

        payload: dict[str, object] = {
            "instrument_id": str(instrument.instrument_id),
            "venue": instrument.venue.value,
            "canonical_symbol": instrument.canonical_symbol,
            "venue_symbol": instrument.venue_symbol,
            "listed_at": instrument.listed_at.isoformat(),
        }
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_instrument",
            aggregate_id=instrument.instrument_id,
            aggregate_revision=None,
            action="instrument.registered",
            outcome=Outcome.SUCCESS,
            actor_subject_id=cmd.actor_subject_id,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    return instrument


async def apply_lifecycle_event(
    pool: asyncpg.Pool,
    cmd: LifecycleEventCommand,
    *,
    current: InstrumentRef,
    refs: ReferenceRepository,
    audit: AuditAppender,
) -> InstrumentRef:
    """§4.2 State machine transition + exactly one audit (success or denial)."""
    if current.instrument_id != cmd.instrument_id:
        raise ValueError("current.instrument_id does not match cmd.instrument_id")

    denial: Exception | None = None
    new_status = current.status

    async with pool.acquire() as conn, conn.transaction():
        try:
            new_status = transition(current.status, cmd.event)
            if cmd.event == "RENAME":
                if not cmd.new_venue_symbol:
                    raise RenameSymbolInUseError("new_venue_symbol is required for RENAME")
                # Format validation only (SymbolNormalizationError) — lookup and storage use the raw symbol as-is.
                to_canonical(current.venue, cmd.new_venue_symbol)
                clash = await refs.get_instrument(
                    conn, current.venue, cmd.new_venue_symbol, cmd.effective_at
                )
                if clash is not None:
                    raise RenameSymbolInUseError(
                        f"new_venue_symbol in use: venue={current.venue.value} "
                        f"symbol={cmd.new_venue_symbol!r}"
                    )
                await refs.add_alias(
                    conn, current.instrument_id, current.venue, cmd.new_venue_symbol
                )
        except (LifecycleTransitionError, RenameSymbolInUseError) as exc:
            denial = exc

        payload: dict[str, object] = {
            "instrument_id": str(current.instrument_id),
            "event": cmd.event,
            "from_status": current.status.value,
            "source_ref": cmd.source_ref,
        }
        if denial is None:
            payload["to_status"] = new_status.value
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_instrument",
            aggregate_id=current.instrument_id,
            aggregate_revision=None,
            action=_ACTIONS[cmd.event],
            outcome=Outcome.DENIED if denial is not None else Outcome.SUCCESS,
            actor_subject_id=cmd.actor_subject_id,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    if denial is not None:
        raise denial

    return current.model_copy(update={"status": new_status})
