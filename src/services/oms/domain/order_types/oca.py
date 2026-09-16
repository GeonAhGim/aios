"""OCA (One-Cancels-All) bracket exit coordination — atomic trigger
determination (pure, thread-safe) (L4 spec §9 EM-19, task-2623).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

Generalizes `oco.py`'s two-leg `resolve_oco`/`OcoGroup` to the N-leg bracket
exit case (`profit`/`loss`/`trail`, task-2623 ADR-2026-09-09-B). Same split
as `oco.py`:
- `resolve_oca`: deterministic replay (single thread, same-tick ambiguity
  resolved via an explicit `priority_order`). Isomorphic to the backtest
  side's `foundation/backtest/domain/fill/order_types.py::resolve_oca` —
  same signature, same decision rule, verified by
  `tests/unit/oms/test_bracket_oca_parity.py` rather than a shared import
  (the two bounded contexts stay independently implemented, same as
  `resolve_oco` already is on both sides).
- `OcaGroup`: the live path, where trigger evaluation for each leg can
  arrive concurrently from different threads/coroutines. `try_trigger`
  holds a lock across the whole read-modify-write so exactly one leg ever
  wins, mirroring `OcoGroup`'s 1000-concurrent-call DoD generalized to N
  legs.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from src.services.oms.domain.order_types.oco import OcoOutcome

__all__ = [
    "OcaResolution",
    "resolve_oca",
    "OcaGroup",
    "bracket_quantity_for_fill",
]


@dataclass(frozen=True, slots=True)
class OcaResolution:
    triggered_leg: str | None
    cancelled_legs: tuple[str, ...]


def resolve_oca(*, triggered: Mapping[str, bool], priority_order: Sequence[str]) -> OcaResolution:
    """The first leg in `priority_order` that is triggered wins; every other
    leg in the group — triggered or not — is cancelled. No leg triggered ->
    `triggered_leg=None`, nothing cancelled yet."""
    if not priority_order:
        raise ValueError("priority_order requires at least one leg")
    if set(triggered) != set(priority_order):
        raise ValueError("triggered and priority_order must cover the same leg set")
    for leg in priority_order:
        if triggered[leg]:
            cancelled = tuple(other for other in priority_order if other != leg)
            return OcaResolution(triggered_leg=leg, cancelled_legs=cancelled)
    return OcaResolution(triggered_leg=None, cancelled_legs=())


@dataclass
class OcaGroup:
    """Live-path N-leg generalization of `OcoGroup`. `legs` fixes the group's
    membership at construction — `try_trigger` on an unknown leg fails
    closed instead of silently admitting it into the group."""

    legs: frozenset[str]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _winner: str | None = field(default=None, repr=False)

    def try_trigger(self, leg: str) -> OcoOutcome:
        """Attempts to confirm `leg` as the trigger. If the group is not yet
        decided, `leg` becomes the winner and TRIGGERED is returned. If
        already decided, the winning leg returns TRIGGERED again
        (idempotent), any other leg always returns CANCELLED — the lock
        spans the entire read-modify-write, so concurrent callers across
        all legs still settle on exactly one winner."""
        if leg not in self.legs:
            raise ValueError(f"unknown leg for this OCA group: {leg!r}")
        with self._lock:
            if self._winner is None:
                self._winner = leg
            return OcoOutcome.TRIGGERED if self._winner == leg else OcoOutcome.CANCELLED

    @property
    def winner(self) -> str | None:
        with self._lock:
            return self._winner


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name} must not be negative or NaN: {value}")


def bracket_quantity_for_fill(*, requested_qty: Decimal, filled_qty: Decimal) -> Decimal:
    """Bracket exit legs must never cover more than what the entry actually
    filled — if the entry only partially fills, the bracket's exit legs are
    sized to `filled_qty`, never the originally requested quantity.
    Isomorphic to the backtest-side function of the same name in
    `foundation/backtest/domain/fill/order_types.py`."""
    _reject_negative_or_nan(requested_qty, "requested_qty")
    _reject_negative_or_nan(filled_qty, "filled_qty")
    return min(requested_qty, filled_qty)
