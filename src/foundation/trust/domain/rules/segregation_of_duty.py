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

As of 2026-09-16, three call sites are wired to this primitive: the
DUAL-mode second-signature check in `core/approval/service.py`, CM-5's
author != approver enforcement in `mandates/application/
activate_revision.py` (the proposer identity it compares against comes
from the existing audit trail — see that module's docstring), and PLT-35's
break-glass approval pre-check (the DB CHECK constraint on
`break_glass_grant` remains the second line of defense). The static scan
in `tests/foundation/unit/trust/test_segregation_of_duty_static.py`
asserts "no code outside this primitive directly compares
actor/counterparty equality" and that all three call sites import this
module (directly, or — for `core/security/break_glass.py`, see below —
via the composition root that injects this function into it) — if any of
them is later rewritten to reinvent the comparison inline, this static
check catches it immediately.

RATCHET-2 (`core-no-io`, ADR-2026-09-10-C) forbids `src/core` from
importing `src/foundation`, so `core/security/break_glass.py` can no
longer import this module directly (task-5311) — it takes the checker as
an injected `SegregationOfDutyChecker` parameter instead (the Protocol +
`SegregationOfDutyViolation` are now owned by
`src.core.security.segregation_of_duty_port`, which this module implements
and re-exports for the two call sites that still import it directly).
`src/api/routers/admin_break_glass.py` is the composition root that wires
`assert_actor_not_counterparty` from here into `break_glass.approve_grant`.
"""

from __future__ import annotations

from src.core.security.segregation_of_duty_port import (
    SegregationOfDutyViolation,
    assert_actor_not_counterparty,
)

__all__ = ["SegregationOfDutyViolation", "assert_actor_not_counterparty"]
