"""DC-3 — Symbol lifecycle transition table (pure state machine).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§4.2 (transition table), §9.2 DC-3.

`domain/reference/lifecycle.py`(LA-7) is a separate state machine that uses
`SymbolStatus`(PENDING/LISTED/SUSPENDED/DELISTED) and events
`LIST/SUSPEND/RESUME/DELIST/RENAME` from the LA-1 contract (contracts/v1).
This module uses `InstrumentLifecycle`(PENDING/ACTIVE/HALTED/DELISTED) from
the DC-1 contract (contracts/v2) and the events in the §4.2 table
(listed/symbol_changed/halted/resumed/delisted/relisted) — it is not
homomorphic and therefore not integrated due to different vocabulary and
row composition (task-1125 decision).

Guards in the §4.2 table (venture verification, new listing registration)
require repository lookups and fall outside the pure function scope.
This function only determines transition eligibility defined by state×event
alone, and the audit event name corresponding to each transition.
"""

from __future__ import annotations

from typing import Final, Literal

from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle

__all__ = [
    "LifecycleEvent",
    "LifecycleTransitionError",
    "RelistRequiresNewInstrumentError",
    "EVENT_LISTED",
    "EVENT_SYMBOL_CHANGED",
    "EVENT_HALTED",
    "EVENT_RESUMED",
    "EVENT_DELISTED",
    "EVENT_RELISTED",
    "AUDIT_EVENT_INSTRUMENT_LISTED",
    "AUDIT_EVENT_LISTING_REPLACED",
    "AUDIT_EVENT_INSTRUMENT_HALTED",
    "AUDIT_EVENT_INSTRUMENT_RESUMED",
    "AUDIT_EVENT_INSTRUMENT_DELISTED",
    "AUDIT_EVENT_INSTRUMENT_RELISTED",
    "transition",
    "audit_event_for",
]

LifecycleEvent = Literal["listed", "symbol_changed", "halted", "resumed", "delisted", "relisted"]

EVENT_LISTED: Final[LifecycleEvent] = "listed"
EVENT_SYMBOL_CHANGED: Final[LifecycleEvent] = "symbol_changed"
EVENT_HALTED: Final[LifecycleEvent] = "halted"
EVENT_RESUMED: Final[LifecycleEvent] = "resumed"
EVENT_DELISTED: Final[LifecycleEvent] = "delisted"
EVENT_RELISTED: Final[LifecycleEvent] = "relisted"

# §4.2 "audit" column — single source of truth (SSOT). Callers (application
# layer) must not rewrite these strings directly; they reference only this
# constant (or `audit_event_for`).
AUDIT_EVENT_INSTRUMENT_LISTED: Final[str] = "instrument.listed"
AUDIT_EVENT_LISTING_REPLACED: Final[str] = "listing.replaced"
AUDIT_EVENT_INSTRUMENT_HALTED: Final[str] = "instrument.halted"
AUDIT_EVENT_INSTRUMENT_RESUMED: Final[str] = "instrument.resumed"
AUDIT_EVENT_INSTRUMENT_DELISTED: Final[str] = "instrument.delisted"
AUDIT_EVENT_INSTRUMENT_RELISTED: Final[str] = "instrument.relisted"

# §4.2 table, 6 rows. (state, event) -> next state. delisted+relisted is
# not an in-place transition for the same instrument_id (new instrument
# issuance) — not present here; `transition` raises
# `RelistRequiresNewInstrumentError` separately.
_TRANSITIONS: Final[dict[tuple[InstrumentLifecycle, LifecycleEvent], InstrumentLifecycle]] = {
    (InstrumentLifecycle.PENDING, EVENT_LISTED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_SYMBOL_CHANGED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_HALTED): InstrumentLifecycle.HALTED,
    (InstrumentLifecycle.HALTED, EVENT_RESUMED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_DELISTED): InstrumentLifecycle.DELISTED,
    (InstrumentLifecycle.HALTED, EVENT_DELISTED): InstrumentLifecycle.DELISTED,
}

# Audit event names for all 6 rows of the §4.2 table (including relisted).
_AUDIT_EVENTS: Final[dict[tuple[InstrumentLifecycle, LifecycleEvent], str]] = {
    (InstrumentLifecycle.PENDING, EVENT_LISTED): AUDIT_EVENT_INSTRUMENT_LISTED,
    (InstrumentLifecycle.ACTIVE, EVENT_SYMBOL_CHANGED): AUDIT_EVENT_LISTING_REPLACED,
    (InstrumentLifecycle.ACTIVE, EVENT_HALTED): AUDIT_EVENT_INSTRUMENT_HALTED,
    (InstrumentLifecycle.HALTED, EVENT_RESUMED): AUDIT_EVENT_INSTRUMENT_RESUMED,
    (InstrumentLifecycle.ACTIVE, EVENT_DELISTED): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.HALTED, EVENT_DELISTED): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.DELISTED, EVENT_RELISTED): AUDIT_EVENT_INSTRUMENT_RELISTED,
}


class LifecycleTransitionError(ValueError):
    """(state, event) pair not in §4.2 table — fail-closed rejection."""


class RelistRequiresNewInstrumentError(LifecycleTransitionError):
    """(DELISTED, relisted) is not an in-place transition for the same
    instrument_id.

    §4.2: "delisted+relisted -> create new instrument (prohibit preserving old
    id)". Callers receiving this exception must issue a new `Instrument` (new
    ULID) instead of updating the existing instrument.
    """


def transition(state: InstrumentLifecycle, event: LifecycleEvent) -> InstrumentLifecycle:
    """§4.2 state machine.

    delisted+relisted requires new instrument issuance, so it is not an
    in-place transition — signals via `RelistRequiresNewInstrumentError`.
    All remaining (state, event) pairs not in the table raise
    `LifecycleTransitionError` (fail-closed).
    """
    if state is InstrumentLifecycle.DELISTED and event == EVENT_RELISTED:
        raise RelistRequiresNewInstrumentError(
            "delisted -> relisted requires new instrument issuance (prohibit preserving old id)"
        )
    try:
        return _TRANSITIONS[(state, event)]
    except (KeyError, TypeError) as exc:
        raise LifecycleTransitionError(f"Transition not allowed: {state!r} + {event!r}") from exc


def audit_event_for(state: InstrumentLifecycle, event: LifecycleEvent) -> str:
    """§4.2 audit event name corresponding to (state, event). Not in the
    table raises `LifecycleTransitionError` (fail-closed) — uses the same
    decision criteria as `transition`."""
    try:
        return _AUDIT_EVENTS[(state, event)]
    except (KeyError, TypeError) as exc:
        raise LifecycleTransitionError(f"Transition not allowed: {state!r} + {event!r}") from exc
