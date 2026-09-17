"""Provider budget rules -- pure functions/value objects, no I/O.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-5
`domain/budget.py` ("per-tenant/per-token cost cap and daily limit, pure"),
§1 "provider neutrality" ("per-tenant cost cap"), §3 error catalog
`AI_BUDGET_EXCEEDED` (429), §9 AI-5 DoD ("over-cap request -> 429"). The 429
mapping itself belongs to the
application layer that will call `reserve()` (AI-6/7/7b's provider
adapters, not yet implemented) -- this module only raises the pure
`BudgetExceededError` that mapping is built on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from uuid import UUID


class BudgetRuleError(Exception):
    """Common base for provider budget rule violations."""


class BudgetExceededError(BudgetRuleError):
    """Maps to spec §3 `AI_BUDGET_EXCEEDED` (429) at the application layer."""

    def __init__(self, *, requested: Decimal, remaining: Decimal, cap: Decimal) -> None:
        self.requested = requested
        self.remaining = remaining
        self.cap = cap
        super().__init__(
            f"budget exceeded: requested {requested} exceeds remaining {remaining} "
            f"(daily_cap {cap})"
        )


@dataclass(frozen=True)
class TenantBudget:
    """Per-tenant, per-token daily cost cap and this-period spend (§2.2
    "per-tenant/per-token cost cap and daily limit"). `period_start` anchors
    what "daily" means for this snapshot -- rolling the period over when the
    wall clock crosses a day boundary is the caller's job (`roll_period`
    below performs the pure reset; deciding *when* to call it, e.g. at the
    top of every request, is the application layer's job, not yet
    implemented). This module only judges a single period at a time."""

    tenant_id: UUID
    token_id: UUID
    daily_cap: Decimal
    spent_today: Decimal
    period_start: date

    def __post_init__(self) -> None:
        if self.daily_cap < 0:
            raise BudgetRuleError("daily_cap must be >= 0")
        if self.spent_today < 0:
            raise BudgetRuleError("spent_today must be >= 0")


def remaining(budget: TenantBudget) -> Decimal:
    """May be negative if `spent_today` was persisted before a `daily_cap`
    reduction -- callers must not clamp this to zero silently; a negative
    remaining means every subsequent `reserve()` call fails closed until
    the period rolls over."""
    return budget.daily_cap - budget.spent_today


def is_new_period(budget: TenantBudget, today: date) -> bool:
    """True once `today` has moved past `period_start` -- the caller must
    `roll_period()` (resetting `spent_today` to 0) before calling
    `reserve()` again, otherwise yesterday's spend keeps suppressing
    today's budget (§2.2 "daily limit")."""
    return today > budget.period_start


def roll_period(budget: TenantBudget, today: date) -> TenantBudget:
    """Pure reset for a new daily period. Rejects fail-closed if `today`
    has not actually advanced past `period_start` -- rolling over a period
    that has not ended would silently erase legitimate spend history."""
    if not is_new_period(budget, today):
        raise BudgetRuleError(
            f"cannot roll period: today={today} has not passed period_start={budget.period_start}"
        )
    return replace(budget, spent_today=Decimal("0"), period_start=today)


def reserve(budget: TenantBudget, cost: Decimal) -> TenantBudget:
    """Pure judgment + reservation: reject fail-closed (`BudgetExceededError`,
    §3 `AI_BUDGET_EXCEEDED`) if `cost` would push `spent_today` past
    `daily_cap`; otherwise return a *new* `TenantBudget` with `spent_today`
    incremented. `TenantBudget` is frozen -- this function never mutates
    the caller's snapshot in place, matching `confirm.verify_and_consume`'s
    "returns a new value, does not mutate" contract.

    Persisting the increment atomically (standard-105 conditional UPDATE,
    read-modify-write under `SELECT ... FOR UPDATE`) is the adapter's job
    (not yet implemented, AI-6+); this function only performs the pure
    arithmetic and the reject.
    """
    if cost < 0:
        raise BudgetRuleError("cost must be >= 0")
    room = remaining(budget)
    if cost > room:
        raise BudgetExceededError(requested=cost, remaining=room, cap=budget.daily_cap)
    return replace(budget, spent_today=budget.spent_today + cost)
