"""Segregation of Duty primitive — PLT-43.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-43,
ADR-2026-09-06-G §9 ("RBAC is a 5-role plane and 'author != approver' is
implemented separately per feature — Charles River uses a feature-level
permission grid").

This module is the single definition of one invariant: "actor !=
counterparty_for(action)". Any feature (mandate rule-bundle governance
CM-5, break-glass approval PLT-35, or any other approval/execution path)
must route through here to reject an attempt by "the requester themself to
approve/execute their own request" — no feature should reinvent
`if a == b: raise ...` on its own.

Unverified: as of 2026-09-07, this repository does not yet implement CM-5
(the author != approver enforcement in `mandates/application/
activate_revision.py` / `propose_amendment.py`) or PLT-35
(`core/security/break_glass.py`) — both are still only spec-level plans
that do not exist as leaves yet. The only place actually migrated and wired
to this primitive today is the DUAL-mode second-signature check in
`core/approval/service.py` (previously reinvented inline). The static scan
in `tests/foundation/unit/trust/test_segregation_of_duty_static.py`
reflects this fact and asserts only the truth as of today: "no code outside
this primitive directly compares actor/counterparty equality" — if CM-5 or
PLT-35 are later built and reinvent this inline instead of routing through
this module, this static check catches it immediately.
"""
from __future__ import annotations

from collections.abc import Hashable


class SegregationOfDutyViolation(Exception):
    """The same subject tried to act as both the actor and counterparty of
    an action."""

    def __init__(self, actor_id: Hashable, action: str) -> None:
        super().__init__(
            f"{action}: actor({actor_id!r})는 자기 자신의 counterparty가 될 수 없습니다."
        )
        self.actor_id = actor_id
        self.action = action


def assert_actor_not_counterparty(
    actor_id: Hashable, counterparty_id: Hashable | None, *, action: str
) -> None:
    """Rejects if actor_id equals this action's counterparty_id.

    If `counterparty_id` is `None` (no one has taken that role yet — e.g.
    before the first DUAL-approval signature, before a mandate draft
    proposal), there is nothing to compare against, so it passes through.
    Equality between the two ids is decided with `==`, so this works as-is
    for any identifier type that supports value equality (`UUID`, `str`,
    `int`, etc.).
    """
    if counterparty_id is not None and actor_id == counterparty_id:
        raise SegregationOfDutyViolation(actor_id, action)


__all__ = ["SegregationOfDutyViolation", "assert_actor_not_counterparty"]
