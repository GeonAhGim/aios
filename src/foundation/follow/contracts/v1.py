"""L4_product_experience_and_discovery_v1.0.md §2.4 UX-13 -- follow subscription contract.

spec line 51: `FollowSubscription{follower_portfolio, source_listing,
sizing_policy, max_notional, paper_only=True}`. `follower_tenant_id` is not in
that gist table (§2.4 is a summary), but calling the risk_gate/mandates gates
requires a tenant_id (FA-0b: one tenant can own several portfolios, so
tenant_id cannot be derived back from portfolio_id alone) -- the minimal
extension this leaf actually needs to be callable.

Invariant UX-A3 ("a follow order also passes the follower's own risk/
compliance gate -- no inheriting the source account's authority") and spec §3
("follow: paper_only=True is invariant. Switching to LIVE is out of scope for
this contract") are both enforced at construction time (`__post_init__`) --
at the point a subscription is created, not as a runtime toggle, so there is
no path that can later flip it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID


class SizingPolicy(str, Enum):
    """How a source signal's sizing is converted into the follower's account
    (spec §2.4 `domain/mirror_rules.py` "weight conversion")."""

    MIRROR_WEIGHT = "MIRROR_WEIGHT"
    """Mirrors the source's target weight (%) as-is -- `max_notional * source_weight_pct`."""
    FIXED_NOTIONAL = "FIXED_NOTIONAL"
    """Uses a fixed `max_notional` amount per signal, regardless of the source's weight."""


@dataclass(frozen=True)
class FollowSubscription:
    """spec §2.4 line 51 contract, plus `follower_tenant_id` (see module docstring)."""

    id: UUID
    follower_tenant_id: UUID
    follower_portfolio: UUID
    source_listing: UUID
    sizing_policy: SizingPolicy
    max_notional: Decimal
    paper_only: Literal[True] = True

    def __post_init__(self) -> None:
        if not self.paper_only:
            raise ValueError(
                "FOLLOW_LIVE_NOT_AUTHORIZED: paper_only=True is a contract invariant -- "
                "switching to LIVE needs its own ADR and is out of scope here (spec §3)."
            )
        if self.max_notional <= 0:
            raise ValueError("FOLLOW_MAX_NOTIONAL_INVALID: max_notional must be positive")


@dataclass(frozen=True)
class SourceSignal:
    """Minimal value object for the source signal being mirrored.

    The marketplace listing/signal contract (MP-1, `signals/contracts/v1.py`)
    does not exist yet (confirmed via git ls-files, 2026-09-17), so this leaf
    cannot author the real listing/signal schema that `source_listing` would
    point to. Instead this carries only the fields `mirror_signal` actually
    consumes -- the real signal-delivery implementation (listing -> signal
    stream) is left to a later leaf (after MP-1).
    """

    source_listing: UUID
    symbol: str
    side: Literal["BUY", "SELL", "FLAT"]
    source_weight_pct: Decimal
    is_active: bool = True
