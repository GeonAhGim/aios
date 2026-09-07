"""OCO (One-Cancels-Other) sibling coordination — atomic trigger
determination (pure, thread-safe) (L4 spec §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

Two forms of determination are needed.
- `resolve_oco`: for deterministic replay (single thread, the ambiguous
  case where both legs satisfy their condition on the same tick) — the
  caller states a conservative assumption via `priority_leg`. Isomorphic to
  the backtest side's BT-6 `resolve_oco`.
- `OcoGroup`: for the live path — trigger evaluation for the two legs can
  actually arrive concurrently from different threads/coroutines (e.g. a
  price tick and a time expiry arriving at the same instant).
  `try_trigger` uses a `threading.Lock` to atomically enforce "whichever leg
  arrives first wins" — once a winner is decided, calling the same leg
  again any number of times returns the same answer (idempotent). DoD:
  across 1000 concurrent calls, zero cases where both legs become TRIGGERED
  at once (prevents double fills).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

OcoLeg = Literal["a", "b"]

__all__ = ["OcoLeg", "OcoOutcome", "OcoResolution", "resolve_oco", "OcoGroup"]


class OcoOutcome(str, Enum):
    TRIGGERED = "TRIGGERED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class OcoResolution:
    triggered_leg: Literal["a", "b", "none"]
    cancelled_leg: Literal["a", "b", "none"]


def resolve_oco(
    *, leg_a_triggered: bool, leg_b_triggered: bool, priority_leg: OcoLeg
) -> OcoResolution:
    """When one side triggers, the other is immediately cancelled. When both
    satisfy their condition within the same tick (e.g. a sharp gap), this
    information alone cannot tell which actually came first — instead of
    silently hiding this ambiguity, the caller is forced to explicitly pick
    a conservative assumption (e.g. stop-loss leg priority) via
    `priority_leg`."""
    if leg_a_triggered and leg_b_triggered:
        if priority_leg == "a":
            return OcoResolution(triggered_leg="a", cancelled_leg="b")
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    if leg_a_triggered:
        return OcoResolution(triggered_leg="a", cancelled_leg="b")
    if leg_b_triggered:
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    return OcoResolution(triggered_leg="none", cancelled_leg="none")


@dataclass
class OcoGroup:
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _winner: OcoLeg | None = field(default=None, repr=False)

    def try_trigger(self, leg: OcoLeg) -> OcoOutcome:
        """Attempts to confirm this leg as the trigger. If the group is not
        yet decided, this leg becomes the winner and TRIGGERED is returned.
        If already decided, the leg matching the winner returns TRIGGERED
        (idempotent re-confirmation), any other leg always returns
        CANCELLED — the lock spans the entire read-modify-write, so even if
        two threads enter concurrently, exactly one winner is chosen."""
        with self._lock:
            if self._winner is None:
                self._winner = leg
            return OcoOutcome.TRIGGERED if self._winner == leg else OcoOutcome.CANCELLED

    @property
    def winner(self) -> OcoLeg | None:
        with self._lock:
            return self._winner
