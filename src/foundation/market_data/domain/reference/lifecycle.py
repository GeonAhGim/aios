"""LA-7 — Symbol lifecycle state machine (pure functions).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-7, §4.2, §9.2 LA-7.

`SymbolStatus` is reused from LA-1 contract (contracts/v1); do not override.
`LifecycleEvent` is a direct copy of the Literal from
`LifecycleEventCommand.event` — it carries no new meaning of its own.

§4.2 guard conditions (open position count == 0, forced-flag, no
`new_venue_symbol` usage) require repository lookups and fall outside
the pure-function scope. This function only decides transition
possibility from state×event alone; guards are the responsibility of
the application layer
(`register_instrument.apply_lifecycle_event`, follow-up leaf).
"""
from __future__ import annotations

from typing import Literal

from src.foundation.market_data.contracts.v1 import SymbolStatus

__all__ = ["LifecycleEvent", "LifecycleTransitionError", "transition"]

LifecycleEvent = Literal["LIST", "SUSPEND", "RESUME", "DELIST", "RENAME"]

_TRANSITIONS: dict[tuple[SymbolStatus, LifecycleEvent], SymbolStatus] = {
    (SymbolStatus.PENDING, "LIST"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "SUSPEND"): SymbolStatus.SUSPENDED,
    (SymbolStatus.SUSPENDED, "RESUME"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.SUSPENDED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.LISTED, "RENAME"): SymbolStatus.LISTED,
}


class LifecycleTransitionError(ValueError):
    """`outcome=DENIED` — (state, event) pair not in §4.2 table (all DELISTED inputs denied)."""


def transition(state: SymbolStatus, event: LifecycleEvent) -> SymbolStatus:
    """§4.2 state machine. Unlisted transitions raise `LifecycleTransitionError` (fail-closed)."""
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as exc:
        raise LifecycleTransitionError(
            f"허용되지 않는 전이: {state.value} + {event}"
        ) from exc
