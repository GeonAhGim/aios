"""U-3a -- per-tenant daily request budget (pure judgment, no I/O).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-3 DoD
("apply a per-session request/token cap and return a clear error on
overflow").

AI-5 (`src/foundation/ai/providers/domain/budget.py`, task-2639, inflight at
commit time) will eventually produce a general budget model that includes
provider cost ($) accounting. AI-6 (cost instrumentation) does not exist yet
either, so there is no way to measure real $ cost accurately right now
(guessing is not acceptable). This module is therefore a narrow stand-in
that only judges a "request count" cap (a per-tenant daily quota). Once
AI-5/6 land, this file is expected to become a thin adapter over that
general domain.

Where the counter (how many requests so far) is stored is not this module's
responsibility -- `application/generate_script.py` etc. hold that state and
only ask this pure function for a verdict (whatever the caller uses to store
the counter -- in-memory, a rate-limit bucket, whatever -- this judgment
logic does not change).
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceededError(Exception):
    """The tenant's daily AI-assistant request cap was exceeded -- the
    router translates this to 429."""

    def __init__(self, *, used: int, cap: int) -> None:
        self.used = used
        self.cap = cap
        super().__init__(f"assistant daily budget exceeded: used={used} cap={cap}")


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    used: int
    cap: int
    remaining: int


def check_daily_budget(*, used_today: int, daily_cap: int) -> BudgetDecision:
    """Allow when `used_today` (the count before this request) is below
    `daily_cap`. A cap of 0 or less is always denied (fail-closed -- a
    misconfigured cap is never interpreted as unlimited)."""
    if daily_cap <= 0:
        return BudgetDecision(allowed=False, used=used_today, cap=daily_cap, remaining=0)
    allowed = used_today < daily_cap
    remaining = max(daily_cap - used_today, 0)
    return BudgetDecision(allowed=allowed, used=used_today, cap=daily_cap, remaining=remaining)


def enforce_daily_budget(*, used_today: int, daily_cap: int) -> BudgetDecision:
    """Same judgment as `check_daily_budget`, but raises on denial -- so
    callers do not repeat `if not decision.allowed: raise ...` everywhere."""
    decision = check_daily_budget(used_today=used_today, daily_cap=daily_cap)
    if not decision.allowed:
        raise BudgetExceededError(used=used_today, cap=daily_cap)
    return decision
